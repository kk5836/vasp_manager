# Copyright (c) Dale Gaines II
# Distributed under the terms of the MIT LICENSE

import json
import logging
import os
from functools import cached_property

import numpy as np
from pymatgen.core import Structure
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

from vasp_manager.utils import NumpyEncoder, pgrep

logger = logging.getLogger(__name__)


class ElasticAnalyzer:
    def __init__(
        self, calc_path=None, cij=None, change_from_vasp=True, rounding_precision=3
    ):
        """
        If calc_path is specified, read cij from the OUTCAR in calc path
        If cij is specified, use it directly
        Args:
            cij (6x6 array-like[float]): stiffness tensor in Voigt notation
                -- careful! VASP does not output stiffness tensors in this notation
            calc_path (str): path to elastic calculation folder
            change_from_vasp (bool): if True, convert stiffness tensor from
                VASP's elastic tensor notation to Voigt notation
            rounding_precision (int): precision to round calculated quantities
        """
        self._cij = cij
        self._calc_path = calc_path
        self.change_from_vasp = change_from_vasp
        self._rounding_precision = rounding_precision
        self._results = None
        self._structure = None
        self._crystal_system = None

    @property
    def calc_path(self):
        if self._calc_path is not None:
            self.calc_path = self._calc_path
        return self._calc_path

    @calc_path.setter
    def calc_path(self, value):
        # make sure cij wasn't already defined
        # if self._cij is not None:
        #     raise Exception("Could not set calc_path as cij was already specified")
        if not os.path.exists(value):
            raise ValueError(f"Could not set calc_path to {value} as it does not exist")
        self._calc_path = value

    @property
    def structure(self):
        if self._structure is None:
            self._structure = Structure.from_file(os.path.join(self.calc_path, "POSCAR"))
        return self._structure

    @property
    def crystal_system(self):
        if self._crystal_system is None:
            sga = SpacegroupAnalyzer(self.structure, symprec=1e-3)
            self._crystal_system = sga.get_crystal_system()
        return self._crystal_system

    @property
    def rounding_precision(self):
        if self._rounding_precision is not None:
            self.rounding_precision = self._rounding_precision
        return self._rounding_precision

    @rounding_precision.setter
    def rounding_precision(self, value):
        if not isinstance(value, int):
            raise ValueError(
                f"Could not set rounding_precision to {value}, rounding precision must"
                " be an int"
            )
        self._rounding_precision = value

    @cached_property
    def elastic_file(self):
        return os.path.join(self.calc_path, "elastic_constants.txt")

    @cached_property
    def outcar_file(self):
        return os.path.join(self.calc_path, "OUTCAR")

    @property
    def cij(self):
        # assumed to be in GPa
        if self._cij is None:
            self.cij = np.round(
                self._read_stiffness_tensor_file(), self.rounding_precision
            )
        else:
            self.cij = self._cij
        return self._cij

    @cij.setter
    def cij(self, values):
        if np.shape(values) != (6, 6):
            raise ValueError
        if np.asarray(values).dtype != float:
            raise ValueError
        self._cij = values

    @cached_property
    def sij(self):
        # DO NOT ROUND
        return self.get_compliance_tensor(self.cij)

    @cached_property
    def b_reuss(self):
        return np.round(self.get_B_Reuss(self.sij), self.rounding_precision)

    @cached_property
    def b_voigt(self):
        return np.round(self.get_B_Voigt(self.cij), self.rounding_precision)

    @cached_property
    def b_vrh(self):
        return np.round(
            self.get_VRH_average(self.b_reuss, self.b_voigt), self.rounding_precision
        )

    @cached_property
    def g_reuss(self):
        return np.round(self.get_G_Reuss(self.sij), self.rounding_precision)

    @cached_property
    def g_voigt(self):
        return np.round(self.get_G_Voigt(self.cij), self.rounding_precision)

    @cached_property
    def g_vrh(self):
        return np.round(
            self.get_VRH_average(self.g_reuss, self.g_voigt), self.rounding_precision
        )

    @cached_property
    def elastically_unstable(self):
        return self.check_elastically_unstable(self.cij, self.crystal_system)

    @staticmethod
    def change_elastic_constants_from_vasp(vasp_elastic_tensor):
        """
        VASP ordering of elastic constants is not the same as those expected
        by my equations below
        We should expect from Voigt notation:
        1: 11 or xx
        2: 22 or yy
        3: 33 or zz
        4: 23 or yz
        5: 13 or xz
        6: 12 or xy
        but VASP differs as it presents 1, 2, 3, 6 (xy), 4 (yz), 5 (xz)
        This function performs swapping to match expectations

        Args:
            vasp_elastic_tensor (6x6 np.array[float])
        Returns:
            elastic_tensor (6x6 np.array[float]): reordered to match Voigt notation
        """
        elastic_tensor = np.asarray(vasp_elastic_tensor).copy()
        for j in range(6):
            elastic_tensor[3, j], elastic_tensor[4, j], elastic_tensor[5, j] = (
                elastic_tensor[4, j],
                elastic_tensor[5, j],
                elastic_tensor[3, j],
            )
        for i in range(6):
            elastic_tensor[i, 3], elastic_tensor[i, 4], elastic_tensor[i, 5] = (
                elastic_tensor[i, 4],
                elastic_tensor[i, 5],
                elastic_tensor[i, 3],
            )
        return elastic_tensor

    @staticmethod
    def get_compliance_tensor(cij):
        """
        Args:
            cij (6x6 np.arrray[float]): stiffness tensor
        Returns:
            sij (6x6 np.arrray[float]): compliance tensor
        """
        sij = np.linalg.inv(cij)
        return sij

    @staticmethod
    def get_VRH_average(mod1, mod2):
        """
        Args:
            mod1 (float):  B or G Voigt
            mode2 (float): B or G Reuss
        Returns:
            VRH_average (float)
        """
        return (mod1 + mod2) / 2.0

    @staticmethod
    def get_B_Reuss(sij):
        """
        Args:
            sij (6x6 np.arrray[float]): compliance tensor
        Returns:
            B_Reuss (float): Reuss bulk modulus
        """
        B_Reuss = 1 / (
            (sij[0, 0] + sij[1, 1] + sij[2, 2]) + 2 * (sij[0, 1] + sij[1, 2] + sij[2, 0])
        )
        return B_Reuss

    @staticmethod
    def get_B_Voigt(cij):
        """
        Args:
            cij (6x6 np.arrray[float]): compliance_tensor
        Returns:
            B_Reuss (float): Reuss bulk modulus
        """
        B_Voigt = (
            (cij[0, 0] + cij[1, 1] + cij[2, 2]) + 2 * (cij[0, 1] + cij[1, 2] + cij[2, 0])
        ) / 9
        return B_Voigt

    @staticmethod
    def get_G_Reuss(sij):
        """
        Args:
            sij (6x6 np.arrray[float]): compliance tensor
        Returns:
            B_Reuss (float): Reuss bulk modulus
        """
        G_Reuss = 15 / (
            4 * (sij[0, 0] + sij[1, 1] + sij[2, 2])
            - 4 * (sij[0, 1] + sij[1, 2] + sij[2, 0])
            + 3 * (sij[3, 3] + sij[4, 4] + sij[5, 5])
        )
        return G_Reuss

    @staticmethod
    def get_G_Voigt(cij):
        """
        Args:
            cij (6x6 np.arrray[float]): stiffness tensor
        Returns:
            G_Reuss (float): Reuss shear modulus
        """
        G_Voigt = (
            (cij[0, 0] + cij[1, 1] + cij[2, 2])
            - (cij[0, 1] + cij[1, 2] + cij[2, 0])
            + 3 * (cij[3, 3] + cij[4, 4] + cij[5, 5])
        ) / 15
        return G_Voigt

    @staticmethod
    def check_elastically_unstable(cij, crystal_system):
        "returns True if compound is elastically unstable"
        c11 = cij[0, 0]
        c12 = cij[0, 1]
        c13 = cij[0, 2]
        c33 = cij[2, 2]
        c44 = cij[3, 3]
        c66 = cij[5, 5]
        # must meet these criteria to be elastically stable
        if crystal_system == "cubic":
            condition_1 = c11 > c12
            condition_2 = c11 + 2 * c12 > 0
            condition_3 = c44 > 0
            conditions = [condition_1, condition_2, condition_3]
        elif crystal_system == "hexagonal":
            condition_1 = c11 > np.abs(c12)
            condition_2 = 2 * c13**2 < c33 * (c11 + c12)
            condition_3 = c44 > 0
            condition_4 = c66 > 0
            conditions = [condition_1, condition_2, condition_3, condition_4]
        else:
            raise NotImplementedError(
                f"Crystal system {crystal_system} not yet implemented"
            )

        if not np.all(conditions):
            logger.warning("-" * 10 + " WARNING: Elastically Unstable " + "-" * 10)
            return True
        else:
            return False

    def _make_stiffness_tensor_file(self):
        """
        Utility function that scrapes OUTCAR for elastic constants
        Writes to elastic_constants.txt
        """
        if not os.path.exists(self.outcar_file):
            raise Exception(
                f"No OUTCAR available at {self.outcar_file} to make stiffness tensor file"
            )
        # need to get elastic dir
        elastic_table = pgrep(
            self.outcar_file,
            str_to_grep="TOTAL ELASTIC MOD",
            stop_after_first_match=True,
            after=8,
            as_string=True,
        )
        with open(self.elastic_file, "w+") as fw:
            fw.write(elastic_table)

    def _read_stiffness_tensor_file(self):
        """
        Reads vasp stiffness tensor from elastic_file

        Returns:
            elastic_tensor (6x6 np.array[float]): stiffness tensor
        """
        self._make_stiffness_tensor_file()

        with open(self.elastic_file, "r") as fr:
            raw_elastic_data = fr.readlines()
        # Skip first 3 rows as they are just header
        # Skip the first column as it contains xx, xy, etc
        elastic_data = [line.strip().split()[1:] for line in raw_elastic_data[3:]]
        # Divide by 10 to get GPa instead of kBar
        elastic_tensor = np.array(elastic_data, dtype=float) / 10.0
        if self.change_from_vasp:
            elastic_tensor = self.change_elastic_constants_from_vasp(elastic_tensor)
        return elastic_tensor

    def _analyze_elastic(self):
        """
        Grabs important quantities from the elastic calculation results

        Args:
            elastic_file (str): filepath
        Returns:
            elastic_dict (dict): dict of extracted info from
                elastic calculation
        """
        elastic_dict = {}
        elastic_dict["B_Reuss"] = self.b_reuss
        elastic_dict["B_Voigt"] = self.b_voigt
        elastic_dict["B_VRH"] = self.b_vrh
        elastic_dict["G_Reuss"] = self.g_reuss
        elastic_dict["G_Voigt"] = self.g_voigt
        elastic_dict["G_VRH"] = self.g_vrh
        elastic_dict["warning"] = self.elastically_unstable
        elastic_dict["elastic_tensor"] = self.cij

        logger.debug(json.dumps(elastic_dict, cls=NumpyEncoder, indent=2))

        return elastic_dict

    @property
    def results(self):
        if self._results is None:
            self._results = self._analyze_elastic()
        return self._results
