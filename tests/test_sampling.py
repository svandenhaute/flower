from dataclasses import asdict
import os
import pytest
import torch
import numpy as np
from parsl.dataflow.futures import AppFuture

from ase import Atoms

from flower.models import NequIPModel
from flower.sampling import BaseWalker, RandomWalker, DynamicWalker, \
        OptimizationWalker, load_walker
from flower.ensemble import Ensemble
from flower.checks import SafetyCheck


def test_save_load(context, dataset, tmpdir):
    walker = DynamicWalker(context, dataset[0], steps=10, step=1)
    path_start = tmpdir / 'start.xyz'
    path_state = tmpdir / 'state.xyz'
    path_pars  = tmpdir / 'DynamicWalker.yaml' # has name of walker class
    futures = walker.save(tmpdir)
    assert os.path.exists(path_start)
    assert os.path.exists(path_state)
    assert os.path.exists(path_pars)
    walker_ = load_walker(context, tmpdir)
    assert type(walker_) == DynamicWalker
    assert np.allclose(
            walker.start_future.result().positions,
            walker_.start_future.result().positions,
            )
    assert np.allclose(
            walker.state_future.result().positions,
            walker_.state_future.result().positions,
            )
    for key, value in asdict(walker.parameters).items():
        assert value == asdict(walker_.parameters)[key]


def test_base_walker(context, dataset):
    walker = BaseWalker(context, dataset[0])
    assert isinstance(walker.state_future, AppFuture)
    assert isinstance(walker.start_future, AppFuture)
    assert walker.state_future != walker.start_future # do not point to same future
    assert isinstance(walker.start_future.result(), Atoms)
    assert isinstance(walker.state_future.result(), Atoms)
    #walker.reset_if_unsafe()
    #assert 

    with pytest.raises(TypeError): # illegal kwarg
        BaseWalker(context, dataset[0], some_illegal_kwarg=0)


def test_random_walker(context, dataset):
    walker = RandomWalker(context, dataset[0], seed=0)

    state = walker.propagate()
    assert isinstance(state, AppFuture)
    assert isinstance(walker.is_reset(), AppFuture)
    assert not walker.is_reset().result()

    walker = RandomWalker(context, dataset[0], seed=0)
    safe_state = walker.propagate(safe_return=True)
    assert np.allclose(
            state.result().get_positions(),
            safe_state.result().get_positions(),
            )

    walker.reset_if_unsafe() # random walker is never unsafe
    assert not walker.is_reset().result()

    walker.tag_future = 'unsafe'
    walker.reset_if_unsafe() # should reset
    assert walker.is_reset().result() # should reset

    state = walker.propagate(model='dummy') # irrelevant kwargs are ignored


def test_ensemble(context, dataset, tmpdir):
    walker = RandomWalker(context, dataset[0])
    nwalkers = 10
    ensemble = Ensemble.from_walker(walker, nwalkers=nwalkers)

    with pytest.raises(AssertionError):
        new_data = ensemble.propagate(5) # nstates should be >= nwalkers
    nstates = 15
    new_data = ensemble.propagate(nstates, checks=[SafetyCheck()]) # always passes
    assert new_data.length().result() == nstates
    for i, walker in enumerate(ensemble.walkers[:int(nstates % nwalkers)]):
        assert walker.parameters.seed == i + (nstates // nwalkers + 1) * nwalkers
    for i, walker in enumerate(ensemble.walkers[int(nstates % nwalkers):]):
        assert walker.parameters.seed == i + (nstates // nwalkers) * nwalkers + nstates % nwalkers

    # no two states should be the same
    for i in range(new_data.length().result() - 1):
        for j in range(i + 1, new_data.length().result()):
            assert not np.allclose(
                    new_data[i].result().get_positions(),
                    new_data[j].result().get_positions(),
                    )

    # test save and load
    ensemble.save(tmpdir)
    ensemble_ = Ensemble.load(context, tmpdir)
    assert ensemble_.nwalkers == nwalkers
    for i, walker in enumerate(ensemble_.walkers[:int(nstates % nwalkers)]):
        assert walker.parameters.seed == i + (nstates // nwalkers + 1) * nwalkers
    for i, walker in enumerate(ensemble_.walkers[int(nstates % nwalkers):]):
        assert walker.parameters.seed == i + (nstates // nwalkers) * nwalkers + nstates % nwalkers
    ndirs = len([f for f in os.listdir(tmpdir) if os.path.isdir(tmpdir / f)])
    assert ndirs == nwalkers


def test_dynamic_walker(context, dataset, nequip_config):
    walker = DynamicWalker(context, dataset[0], steps=10, step=1)
    model = NequIPModel(context, nequip_config)
    model.initialize(dataset[:3])
    model.deploy()
    state, trajectory = walker.propagate(model=model, keep_trajectory=True)
    assert trajectory.length().result() == 11
    assert np.allclose(
            trajectory[0].result().get_positions(), # initial structure
            walker.start_future.result().get_positions(),
            )
    assert walker.tag_future.result() == 'safe'
    assert not np.allclose(
            walker.start_future.result().get_positions(),
            state.result().get_positions(),
            )
    walker.parameters.force_threshold = 0.001
    walker.parameters.steps           = 1
    walker.parameters.step            = 1
    state = walker.propagate(model=model)
    assert walker.tag_future.result() == 'unsafe' # raised ForceExceededException


@pytest.mark.skipif(not torch.cuda.is_available(), reason='requires GPU')
def test_optimization(context, dataset, nequip_config):
    training = dataset[:15]
    validate = dataset[15:]
    model = NequIPModel(context, nequip_config)
    model.initialize(training)
    model.train(training, validate)
    model.deploy()

    walker = OptimizationWalker(context, dataset[0], optimize_cell=False, fmax=1e-1)
    final = walker.propagate(model=model)
    assert np.all(np.abs(final.result().positions - dataset[0].result().positions) < 0.5)
    assert not np.all(np.abs(final.result().positions - dataset[0].result().positions) < 0.05) # they have to have moved
    walker.parameters.fmax = 1e-3
    final_ = walker.propagate(model=model)
    assert np.all(np.abs(final.result().positions - final_.result().positions) < 0.05) # moved much less