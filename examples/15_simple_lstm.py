# -*- coding: utf-8 -*-

# (C) Copyright 2020, 2021 IBM. All Rights Reserved.
#
# This code is licensed under the Apache License, Version 2.0. You may
# obtain a copy of this license in the LICENSE.txt file in the root directory
# of this source tree or at http://www.apache.org/licenses/LICENSE-2.0.
#
# Any modifications or derivative works of this code must retain this
# copyright notice, and modified files need to carry a notice indicating
# that they have been altered from the originals.

"""aihwkit example 15: hardware-aware training of analog RNN model.

This experiment performs hardware-aware training of an analog RNN on
a simple temporal sequence. The experiment plots training perplexity,
inference results on the test dataset using analog hardware, and inference
results over time using analog hardware and drift compensation.
"""
# pylint: disable=invalid-name

import os
from collections import namedtuple

import matplotlib.pyplot as plt
import numpy as np

# Imports from PyTorch.
import torch
from torch import nn

# Imports from aihwkit.
from aihwkit.nn import AnalogRNN, AnalogLSTMCell, AnalogGRUCell, AnalogVanillaRNNCell
from aihwkit.optim import AnalogSGD
from aihwkit.simulator.configs import SingleRPUConfig
from aihwkit.simulator.configs import InferenceRPUConfig
from aihwkit.simulator.configs.utils import (
    WeightNoiseType, WeightClipType, WeightModifierType)
from aihwkit.simulator.presets import GokmenVlasovPreset
from aihwkit.inference import PCMLikeNoiseModel, GlobalDriftCompensation
from aihwkit.nn import AnalogLinear, AnalogSequential

LEARNING_RATE = 0.05
NUM_LAYERS = 1
INPUT_SIZE = 1
EMBED_SIZE = 20
HIDDEN_SIZE = 50
OUTPUT_SIZE = 1
DROPOUT_RATIO = 0.0
NOISE = 0.0

EPOCHS = 100
BATCH_SIZE = 5
SEQ_LEN = 501
RNN_CELL = AnalogVanillaRNNCell #type of RNN cell
WITH_EMBEDDING = True  # RNN with embedding
WITH_BIDIR = True
USE_ANALOG_TRAINING = False  # or hardware-aware training

if USE_ANALOG_TRAINING:
    # Define a RPU configuration for analog training
    rpu_config = SingleRPUConfig(device=GokmenVlasovPreset())

else:
    # Define an RPU configuration using inference/hardware-aware training tile
    rpu_config = InferenceRPUConfig()
    rpu_config.forward.out_res = -1.  # Turn off (output) ADC discretization.
    rpu_config.forward.w_noise_type = WeightNoiseType.ADDITIVE_CONSTANT
    rpu_config.forward.w_noise = 0.02  # Short-term w-noise.

    rpu_config.clip.type = WeightClipType.FIXED_VALUE
    rpu_config.clip.fixed_value = 1.0
    rpu_config.modifier.pdrop = 0.03  # Drop connect.
    rpu_config.modifier.type = WeightModifierType.ADD_NORMAL  # Fwd/bwd weight noise.
    rpu_config.modifier.std_dev = 0.1
    rpu_config.modifier.rel_to_actual_wmax = True

    # Inference noise model.
    rpu_config.noise_model = PCMLikeNoiseModel(g_max=25.0)

    # drift compensation
    rpu_config.drift_compensation = GlobalDriftCompensation()


# Path to store results
RESULTS = os.path.join(os.getcwd(), 'results', 'RNN')
os.makedirs(RESULTS, exist_ok=True)

# Make dataset
x = torch.linspace(0, 8*np.pi, SEQ_LEN)
y = torch.sin(x)*torch.cos(0.5*x) + 0.5
y_in_1d = y[0:SEQ_LEN-1]
y_out_1d = y[1:SEQ_LEN]

y_in_2d, y_out_2d = [], []
for i in range(BATCH_SIZE):
    y_in_2d.append(torch.roll(y_in_1d, shifts=100*i, dims=0) + NOISE*torch.rand(y_in_1d.shape))
    y_out_2d.append(torch.roll(y_out_1d, shifts=100*i, dims=0) + NOISE*torch.rand(y_out_1d.shape))
y_in = torch.stack(y_in_2d, dim=0).transpose(0, 1).unsqueeze(2)
y_out = torch.stack(y_out_2d, dim=0).transpose(0, 1).unsqueeze(2)


# Various RNN Network definitions

class AnalogBidirRNNNetwork(AnalogSequential):
    """Analog Bidirectional RNN Network definition using AnalogLinear for embedding and decoder."""

    def __init__(self):
        super().__init__()
        self.dropout = nn.Dropout(DROPOUT_RATIO)
        self.embedding = AnalogLinear(INPUT_SIZE, EMBED_SIZE, rpu_config=rpu_config)
        self.rnn = AnalogRNN(RNN_CELL, EMBED_SIZE, HIDDEN_SIZE, bidir=True, num_layers=1,
                               dropout=DROPOUT_RATIO, bias=True,
                               rpu_config=rpu_config)
        self.decoder = AnalogLinear(2*HIDDEN_SIZE, OUTPUT_SIZE, bias=True)

    def forward(self, x_in, in_states=None):  # pylint: disable=arguments-differ
        embed = self.dropout(self.embedding(x_in))
        out, out_states = self.rnn(embed, in_states)
        out = self.dropout(self.decoder(out))
        return out, out_states

class AnalogBidirRNNNetwork_noEmbedding(AnalogSequential):
    """Analog Bidirectional RNN Network definition without embedding layer and using AnalogLinear for decoder."""

    def __init__(self):
        super().__init__()
        self.dropout = nn.Dropout(DROPOUT_RATIO)
        self.rnn = AnalogRNN(RNN_CELL, INPUT_SIZE, HIDDEN_SIZE, bidir=True, num_layers=1,
                               dropout=DROPOUT_RATIO, bias=True,
                               rpu_config=rpu_config)
        self.decoder = AnalogLinear(2*HIDDEN_SIZE, OUTPUT_SIZE, bias=True,
                                    rpu_config=rpu_config)

    def forward(self, x_in, in_states=None):  # pylint: disable=arguments-differ
        """ Forward pass """
        out, out_states = self.rnn(x_in, in_states)
        out = self.dropout(self.decoder(out))
        return out, out_states

class AnalogRNNNetwork(AnalogSequential):
    """Analog RNN Network definition using AnalogLinear for embedding and decoder."""

    def __init__(self):
        super().__init__()
        self.dropout = nn.Dropout(DROPOUT_RATIO)
        self.embedding = AnalogLinear(INPUT_SIZE, EMBED_SIZE, rpu_config=rpu_config)
        self.rnn = AnalogRNN(RNN_CELL, EMBED_SIZE, HIDDEN_SIZE, bidir=False, num_layers=1,
                               dropout=DROPOUT_RATIO, bias=True,
                               rpu_config=rpu_config)
        self.decoder = AnalogLinear(HIDDEN_SIZE, OUTPUT_SIZE, bias=True)

    def forward(self, x_in, in_states=None):  # pylint: disable=arguments-differ
        embed = self.dropout(self.embedding(x_in))
        out, out_states = self.rnn(embed, in_states)
        out = self.dropout(self.decoder(out))
        return out, out_states


class AnalogRNNNetwork_noEmbedding(AnalogSequential):
    """Analog RNN Network definition without embedding layer and using AnalogLinear for decoder."""

    def __init__(self):
        super().__init__()
        self.dropout = nn.Dropout(DROPOUT_RATIO)
        self.rnn = AnalogRNN(RNN_CELL, INPUT_SIZE, HIDDEN_SIZE, bidir=False, num_layers=1,
                               dropout=DROPOUT_RATIO, bias=True,
                               rpu_config=rpu_config)
        self.decoder = AnalogLinear(HIDDEN_SIZE, OUTPUT_SIZE, bias=True,
                                    rpu_config=rpu_config)

    def forward(self, x_in, in_states=None):  # pylint: disable=arguments-differ
        """ Forward pass """
        out, out_states = self.rnn(x_in, in_states)
        out = self.dropout(self.decoder(out))
        return out, out_states

if WITH_EMBEDDING:
    if WITH_BIDIR:
        model = AnalogBidirRNNNetwork()
    else:
        model = AnalogRNNNetwork()
else:
    if WITH_BIDIR:
        model = AnalogBidirRNNNetwork_noEmbedding()
    else:
        model = AnalogRNNNetwork_noEmbedding()

optimizer = AnalogSGD(model.parameters(), lr=LEARNING_RATE)
optimizer.regroup_param_groups(model)
criterion = nn.MSELoss()

# train
losses = []
for i in range(EPOCHS):
    optimizer.zero_grad()
    pred, states = model(y_in)

    loss = criterion(pred, y_out)
    print('Epoch = %d: Train Perplexity = %f' % (i, np.exp(loss.detach().numpy())))

    loss.backward()
    optimizer.step()

    losses.append(loss.detach().cpu())

plt.figure()
plt.plot(np.exp(np.asarray(losses)), '-b')
plt.xlabel('# Epochs')
plt.ylabel('Perplexity [1]')
plt.ylim([1.0, 1.4])
plt.savefig(os.path.join(RESULTS, 'train_perplexity'))
plt.close()

# Test.
model.eval()
pred, states = model(y_in)
loss = criterion(pred, y_out)
print("Test Perplexity = %f" % (np.exp(loss.detach().numpy())))

plt.figure()
plt.plot(y_out[:, 0, 0], '-b')
plt.plot(pred.detach().numpy()[:, 0, 0], '-g')
plt.legend(['truth', 'prediction'])
plt.savefig(os.path.join(RESULTS, 'test'))
plt.close()

# Drift test.
plt.figure()
plt.plot(y_out[:, 0, 0], '-b', label='truth')
for t_inference in [0., 1., 20., 1000., 1e5]:
    model.drift_analog_weights(t_inference)
    pred_drift, states = model(y_in)
    plt.plot(pred_drift[:, 0, 0].detach().cpu().numpy(), label='t = ' + str(t_inference) + ' s')
plt.legend()
plt.savefig(os.path.join(RESULTS, 'drift'))
plt.close()
