import requests
import pytest
import os
import molmod
import numpy as np
from parsl.dataflow.futures import AppFuture
from parsl.app.futures import DataFuture

from pymatgen.io.cp2k.inputs import Cp2kInput

from ase import Atoms
from ase.io.extxyz import write_extxyz

from psiflow.data import FlowAtoms, parse_reference_logs
from psiflow.reference import EMTReference, CP2KReference
from psiflow.reference._cp2k import insert_filepaths_in_input, \
        insert_atoms_in_input
from psiflow.data import Dataset
from psiflow.execution import ReferenceExecutionDefinition


@pytest.fixture
def fake_cp2k_input():
    return  """
&FORCE_EVAL
   METHOD Quickstep
   STRESS_TENSOR ANALYTICAL
   &DFT
      UKS  F
      MULTIPLICITY  1
      BASIS_SET_FILE_NAME  /user/gent/425/vsc42527/scratch/cp2k/SOURCEFILES/BASISSETS
      POTENTIAL_FILE_NAME  /user/gent/425/vsc42527/scratch/cp2k/SOURCEFILES/GTH_POTENTIALS
      &XC
         &VDW_POTENTIAL
            &PAIR_POTENTIAL
               PARAMETER_FILE_NAME  /user/gent/425/vsc42527/scratch/cp2k/SOURCEFILES/dftd3.dat
            &END PAIR_POTENTIAL
         &END VDW_POTENTIAL
      &END XC
   &END DFT
   &SUBSYS
      &KIND Al
         ELEMENT  H
         BASIS_SET foo
         POTENTIAL bar
      &END KIND
      &COORD
         H 4.0 0.0 0.0
      &END COORD
    &END SUBSYS
&END FORCE_EVAL
"""


@pytest.fixture
def cp2k_data():
    basis     = requests.get('https://raw.githubusercontent.com/cp2k/cp2k/v9.1.0/data/BASIS_MOLOPT_UZH').text
    dftd3     = requests.get('https://raw.githubusercontent.com/cp2k/cp2k/v9.1.0/data/dftd3.dat').text
    potential = requests.get('https://raw.githubusercontent.com/cp2k/cp2k/v9.1.0/data/POTENTIAL_UZH').text
    return {
            'BASIS_SET_FILE_NAME': basis,
            'POTENTIAL_FILE_NAME': potential,
            'PARAMETER_FILE_NAME': dftd3,
            }


@pytest.fixture
def cp2k_input():
    return """
&FORCE_EVAL
   METHOD Quickstep
   STRESS_TENSOR ANALYTICAL
   &DFT
      UKS  F
      MULTIPLICITY  1
      BASIS_SET_FILE_NAME  dummy
      POTENTIAL_FILE_NAME  dummy
      &SCF
         MAX_SCF  10
         MAX_DIIS  8
         EPS_SCF  1.0E-06
         SCF_GUESS  RESTART
         &OT
            MINIMIZER  CG
            PRECONDITIONER  FULL_SINGLE_INVERSE
         &END OT
         &OUTER_SCF T
            MAX_SCF  10
            EPS_SCF  1.0E-06
         &END OUTER_SCF
      &END SCF
      &QS
         METHOD  GPW
         EPS_DEFAULT  1.0E-4
         EXTRAPOLATION  USE_GUESS
      &END QS
      &MGRID
         REL_CUTOFF [Ry]  60.0
         NGRIDS  5
         CUTOFF [Ry] 1000
      &END MGRID
      &XC
         DENSITY_CUTOFF   1.0E-10
         GRADIENT_CUTOFF  1.0E-10
         TAU_CUTOFF       1.0E-10
         &XC_FUNCTIONAL PBE
         &END XC_FUNCTIONAL
         &VDW_POTENTIAL
            POTENTIAL_TYPE  PAIR_POTENTIAL
            &PAIR_POTENTIAL
               TYPE  DFTD3(BJ)
               PARAMETER_FILE_NAME  parameter
               REFERENCE_FUNCTIONAL PBE
               R_CUTOFF  25
            &END PAIR_POTENTIAL
         &END VDW_POTENTIAL
      &END XC
   &END DFT
   &SUBSYS
      &KIND H
         ELEMENT  H
         BASIS_SET TZVP-MOLOPT-PBE-GTH-q1
         POTENTIAL GTH-PBE-q1
      &END KIND
   &END SUBSYS
!   &PRINT
!      &STRESS_TENSOR ON
!      &END STRESS_TENSOR
!      &FORCES
!      &END FORCES
!   &END PRINT
&END FORCE_EVAL
"""


def test_reference_emt(context, dataset, tmp_path):
    reference = EMTReference(context)
    # modify dataset to include states for which EMT fails:
    _ = reference.evaluate(dataset).as_list()
    assert reference.data_failed.length().result() == 0
    assert len(reference.logs.result()) == 0
    atoms_list = dataset.as_list()
    atoms_list[6].numbers[1] = 90
    atoms_list[9].numbers[1] = 3
    dataset_ = Dataset(context, atoms_list)
    evaluated = reference.evaluate(dataset_)
    assert evaluated.length().result() == len(atoms_list)
    assert len(reference.logs.result()) == 2 # after join app execution
    assert reference.data_failed.length().result() == 2

    atoms = reference.evaluate(dataset_[5]).result()
    assert type(atoms) == FlowAtoms
    assert atoms.reference_status == True
    atoms = reference.evaluate(dataset_[6]).result()
    assert type(atoms) == FlowAtoms
    assert atoms.reference_status == False
    assert atoms.reference_log == reference.logs.result()[0]
    assert atoms.reference_log != reference.logs.result()[1]


def test_cp2k_insert_filepaths(fake_cp2k_input):
    filepaths = {
            'BASIS_SET_FILE_NAME': ['basisset0', 'basisset1'],
            'POTENTIAL_FILE_NAME': 'potential',
            'PARAMETER_FILE_NAME': 'parameter',
            }
    target_input = """
&FORCE_EVAL
   METHOD Quickstep
   STRESS_TENSOR ANALYTICAL
   &DFT
      UKS  F
      MULTIPLICITY  1
      BASIS_SET_FILE_NAME  basisset0
      BASIS_SET_FILE_NAME  basisset1
      POTENTIAL_FILE_NAME  potential
      &XC
         &VDW_POTENTIAL
            &PAIR_POTENTIAL
               PARAMETER_FILE_NAME  parameter
            &END PAIR_POTENTIAL
         &END VDW_POTENTIAL
      &END XC
   &END DFT
   &SUBSYS
      &KIND Al
         ELEMENT  H
         BASIS_SET foo
         POTENTIAL bar
      &END KIND
      &COORD
         H 4.0 0.0 0.0
      &END COORD
    &END SUBSYS
&END FORCE_EVAL
"""
    target = Cp2kInput.from_string(target_input)
    sample = Cp2kInput.from_string(insert_filepaths_in_input(fake_cp2k_input, filepaths))
    assert str(target) == str(sample)


def test_cp2k_insert_atoms(tmp_path, fake_cp2k_input):
    atoms = FlowAtoms(numbers=np.ones(3), cell=np.eye(3), positions=np.eye(3), pbc=True)
    sample = Cp2kInput.from_string(insert_atoms_in_input(fake_cp2k_input, atoms))
    assert 'COORD' in sample['FORCE_EVAL']['SUBSYS'].subsections.keys()
    assert 'CELL' in sample['FORCE_EVAL']['SUBSYS'].subsections.keys()
    natoms = len(sample['FORCE_EVAL']['SUBSYS']['COORD'].keywords['H'])
    assert natoms == 3


def test_cp2k_success(context, cp2k_input, cp2k_data):
    reference = CP2KReference(context, cp2k_input=cp2k_input, cp2k_data=cp2k_data)
    atoms = FlowAtoms( # simple H2 at ~optimized interatomic distance
            numbers=np.ones(2),
            cell=5 * np.eye(3),
            positions=np.array([[0, 0, 0], [0.74, 0, 0]]),
            pbc=True,
            )
    dataset = Dataset(context, [atoms])
    evaluated = reference.evaluate(dataset[0])
    assert isinstance(evaluated, AppFuture)
    # calculation will fail if time_per_singlepoint in execution definition is too low!
    assert evaluated.result().reference_status == True
    assert 'energy' in evaluated.result().info.keys()
    assert 'stress' in evaluated.result().info.keys()
    assert 'forces' in evaluated.result().arrays.keys()
    assert np.allclose(
            -1.165271084838365 / molmod.units.electronvolt,
            evaluated.result().info['energy'],
            )
    forces_reference = np.array([[0.01218794, 0.00001251, 0.00001251],
            [-0.01215503, 0.00001282, 0.00001282]])
    forces_reference /= molmod.units.electronvolt
    forces_reference *= molmod.units.angstrom
    assert np.allclose(forces_reference, evaluated.result().arrays['forces'])
    stress_reference = np.array([
             [4.81790309081E-01,   7.70485237955E-05,   7.70485237963E-05],
             [7.70485237955E-05,  -9.50069820373E-03,   1.61663002757E-04],
             [7.70485237963E-05,   1.61663002757E-04,  -9.50069820373E-03]])
    stress_reference *= 1000
    assert np.allclose(stress_reference, evaluated.result().info['stress'])

    # check number of mpi processes
    content = evaluated.result().reference_log
    ncores = context[ReferenceExecutionDefinition].ncores
    lines = content.split('\n')
    for line in lines:
        if 'Total number of message passing processes' in line:
            nprocesses = int(line.split()[-1])
        #print(line)
        if 'Number of threads for this process' in line:
            nthreads = int(line.split()[-1])
    assert nprocesses == ncores
    assert nthreads == 1 # hardcoded into app


def test_cp2k_failure(context, cp2k_data):
    cp2k_input = """
&FORCE_EVAL
   METHOD Quickstep
   STRESS_TENSOR ANALYTICAL
   &DFT
      UKS  F
      MULTIPLICITY  1
      BASIS_SET_FILE_NAME  dummy
      POTENTIAL_FILE_NAME  dummy
      &SCF
         MAX_SCF  10
         MAX_DIIS  8
         EPS_SCF  1.0E-01
         SCF_GUESS  RESTART
         &OT
            MINIMIZER  CG
            PRECONDITIONER  FULL_SINGLE_INVERSE
         &END OT
         &OUTER_SCF T
            MAX_SCF  10
            EPS_SCF  1.0E-01
         &END OUTER_SCF
      &END SCF
      &QS
         METHOD  GPW
         EPS_DEFAULT  1.0E-4
         EXTRAPOLATION  USE_GUESS
      &END QS
      &MGRID
         REL_CUTOFF [Ry]  60.0
         NGRIDS  5
         CUTOFF [Ry] 200
      &END MGRID
      &XC
         DENSITY_CUTOFF   1.0E-10
         GRADIENT_CUTOFF  1.0E-10
         TAU_CUTOFF       1.0E-10
         &XC_FUNCTIONAL PBE
         &END XC_FUNCTIONAL
         &VDW_POTENTIAL
            POTENTIAL_TYPE  PAIR_POTENTIAL
            &PAIR_POTENTIAL
               TYPE  DFTD3(BJ)
               PARAMETER_FILE_NAME  parameter
               REFERENCE_FUNCTIONAL PBE
               R_CUTOFF  25
            &END PAIR_POTENTIAL
         &END VDW_POTENTIAL
      &END XC
   &END DFT
   &SUBSYS
      &KIND H
         ELEMENT  H
         BASIS_SET XXXXXXXXXX
         POTENTIAL GTH-PBE-q1
      &END KIND
   &END SUBSYS
   &PRINT
      &STRESS_TENSOR ON
      &END STRESS_TENSOR
      &FORCES
      &END FORCES
   &END PRINT
&END FORCE_EVAL
""" # incorrect input file
    reference = CP2KReference(context, cp2k_input=cp2k_input, cp2k_data=cp2k_data)
    atoms = FlowAtoms( # simple H2 at ~optimized interatomic distance
            numbers=np.ones(2),
            cell=5 * np.eye(3),
            positions=np.array([[0, 0, 0], [0.74, 0, 0]]),
            pbc=True,
            )
    evaluated = reference.evaluate(atoms)
    assert isinstance(evaluated, AppFuture)
    assert evaluated.result().reference_status == False
    assert 'energy' not in evaluated.result().info.keys()
    log = evaluated.result().reference_log
    assert 'ABORT' in log # verify error is captured
    assert 'requested basis set' in log
    parsed = parse_reference_logs([evaluated.result()])
    assert 'ABORT' in parsed # verify error is captured
    assert 'requested basis set' in parsed
    assert 'INDEX 00000 - ' in parsed
    assert reference.logs.result()[0] == log


def test_cp2k_timeout(context, cp2k_data, cp2k_input):
    reference = CP2KReference(context, cp2k_input=cp2k_input, cp2k_data=cp2k_data)
    atoms = FlowAtoms( # simple H2 at ~optimized interatomic distance
            numbers=np.ones(2),
            cell=20 * np.eye(3), # box way too large
            positions=np.array([[0, 0, 0], [3, 0, 0]]),
            pbc=True,
            )
    evaluated = reference.evaluate(atoms)
    assert isinstance(evaluated, AppFuture)
    assert evaluated.result().reference_status == False
    assert 'energy' not in evaluated.result().info.keys()
