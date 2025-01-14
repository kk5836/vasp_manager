# Copyright (c) Dale Gaines II
# Distributed under the terms of the MIT LICENSE

import logging
import os
from functools import cached_property

from vasp_manager.analyzer import ElasticAnalyzer
from vasp_manager.calculation_manager.base import BaseCalculationManager
from vasp_manager.utils import pgrep, ptail
from vasp_manager.vasp_input_creator import VaspInputCreator

logger = logging.getLogger(__name__)


class ElasticCalculationManager(BaseCalculationManager):
    """
    Runs elastic deformation job workflow for a single material
    """

    def __init__(
        self,
        material_path,
        to_rerun,
        to_submit,
        ignore_personal_errors=True,
        from_scratch=False,
        tail=5,
    ):
        """
        For material_path, to_rerun, to_submit, ignore_personal_errors, and from_scratch,
        see BaseCalculationManager

        Args:
            tail (int): number of last lines to log in debugging if job failed
        """
        self.tail = tail
        super().__init__(
            material_path=material_path,
            to_rerun=to_rerun,
            to_submit=to_submit,
            ignore_personal_errors=ignore_personal_errors,
            from_scratch=from_scratch,
        )
        self._is_done = None
        self._results = None

    @cached_property
    def mode(self):
        return "elastic"

    @cached_property
    def poscar_source_path(self):
        poscar_source_path = os.path.join(self.material_path, "rlx", "CONTCAR")
        return poscar_source_path

    def setup_calc(self, increase_nodes=False):
        """
        Runs elastic constants routine through VASP

        By default, requires relaxation (as the elastic constants routine needs
            the cell to be nearly at equilibrium)
        """
        vasp_input_creator = VaspInputCreator(
            self.calc_path,
            mode=self.mode,
            poscar_source_path=self.poscar_source_path,
            name=self.material_name,
            increase_nodes=increase_nodes,
        )
        vasp_input_creator.create()

        if self.to_submit:
            job_submitted = self.submit_job()
            # job status returns True if sucessfully submitted, else False
            if not job_submitted:
                self.setup_calc()

    def check_calc(self):
        """
        Checks result of elastic calculation

        Returns:
            elastic_successful (bool): if True, elastic calculation completed
                successfully
        """
        if not self.job_complete:
            logger.info(f"{self.mode.upper()} job not finished")
            return False

        stdout_path = os.path.join(self.calc_path, "stdout.txt")
        if not os.path.exists(stdout_path):
            # shouldn't get here unless function was called with submit=False
            logger.info(f"{self.mode.upper()} Calculation: No stdout.txt available")
            if self.to_rerun:
                # setup_elastic(elastic_path, submit=submit, increase_nodes=False)
                self.setup_calc(increase_nodes=False)
            return False

        grep_output = pgrep(stdout_path, str_to_grep="Total")
        last_grep_line = grep_output[-1].strip().split()
        # last grep line looks something like 'Total: 36/ 36'
        finished_deformations = int(last_grep_line[-2].replace("/", ""))
        total_deformations = int(last_grep_line[-1])
        logger.debug(last_grep_line)
        if not finished_deformations == total_deformations:
            tail_output = ptail(stdout_path, n_tail=self.tail, as_string=True)
            logger.info(tail_output)
            logger.info(f"{self.mode.upper()} Calculation: FAILED")
            if self.to_rerun:
                # increase nodes as its likely the calculation failed
                # setup_elastic(elastic_path, submit=submit, increase_nodes=True)
                self.setup_calc(increase_nodes=True)
            return False

        logger.info(f"{self.mode.upper()} Calculation: Success")
        return True

    @property
    def is_done(self):
        if self._is_done is None:
            self._is_done = self.check_calc()
        return self._is_done

    @property
    def results(self):
        if not self.is_done:
            return None
        try:
            self._results = self._analyze_elastic()
        except Exception as e:
            logger.warning(e)
            self._results = None
        return self._results

    def _analyze_elastic(self):
        """
        Gets results from elastic calculation
        """
        ea = ElasticAnalyzer(calc_path=self.calc_path)
        return ea.results
