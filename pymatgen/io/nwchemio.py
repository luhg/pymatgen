#!/usr/bin/env python

"""
This module implements input and output processing from Nwchem.
"""

from __future__ import division

__author__ = "Shyue Ping Ong"
__copyright__ = "Copyright 2012, The Materials Project"
__version__ = "0.1"
__maintainer__ = "Shyue Ping Ong"
__email__ = "shyuep@gmail.com"
__date__ = "6/5/13"


import re

from pymatgen.core import Molecule
import pymatgen.core.physical_constants as phyc
from pymatgen.util.io_utils import zopen
from pymatgen.serializers.json_coders import MSONable


class NwTask(MSONable):
    """
    Base task for Nwchem.
    """

    theories = {"scf": "Hartree-Fock",
                "dft": "DFT",
                "sodft": "Spin-Orbit DFT",
                "mp2": "MP2 using a semi-direct algorithm",
                "direct_mp2": "MP2 using a full-direct algorithm",
                "rimp2": "MP2 using the RI approximation",
                "ccsd": "Coupled-cluster single and double excitations",
                "ccsd(t)": "Coupled-cluster linearized triples approximation",
                "ccsd+t(ccsd)": "Fourth order triples contribution",
                "mcscf": "Multiconfiguration SCF",
                "selci": "Selected CI with perturbation correction",
                "md": "Classical molecular dynamics simulation",
                "pspw": "Pseudopotential plane-wave DFT for molecules and "
                        "insulating solids using NWPW",
                "band": "Pseudopotential plane-wave DFT for solids using NWPW",
                "tce": "Tensor Contraction Engine"}

    operations = {"energy": "Evaluate the single point energy.",
                  "gradient": "Evaluate the derivative of the energy with "
                              "respect to nuclear coordinates.",
                  "optimize": "Minimize the energy by varying the molecular "
                              "structure.",
                  "saddle": "Conduct a search for a transition state (or "
                            "saddle point).",
                  "hessian": "Compute second derivatives.",
                  "frequencies": "Compute second derivatives and print out an "
                                 "analysis of molecular vibrations.",
                  "freq": "Same as frequencies.",
                  "vscf": "Compute anharmonic contributions to the "
                          "vibrational modes.",
                  "property": "Calculate the properties for the wave "
                              "function.",
                  "dynamics": "Perform classical molecular dynamics.",
                  "thermodynamics": "Perform multi-configuration "
                                    "thermodynamic integration using "
                                    "classical MD."}

    def __init__(self, mol, charge=None, spin_multiplicity=None,
                 title=None, theory="dft", operation="optimize",
                 basis_set="6-31++G**", theory_directives=None):
        """
        Very flexible arguments to support many types of potential setups.
        Users should use more friendly static methods unless they need the
        flexibility.

        Args:
            mol:
                Input molecule
            charge:
                Charge of the molecule. If None, charge on molecule is used.
                Defaults to None. This allows the input file to be set a
                charge independently from the molecule itself.
            spin_multiplicity:
                Spin multiplicity of molecule. Defaults to None,
                which means that the spin multiplicity is set to 1 if the
                molecule has no unpaired electrons and to 2 if there are
                unpaired electrons.
            title:
                Title for the task. Defaults to None, which means a title
                based on the formula, theory and operation of the task is
                autogenerated.
            theory:
                The theory used for the task. Defaults to "dft".
            operation:
                The operation for the task. Defaults to "optimize".
            basis_set:
                The basis set used for the task. It can either be a
                string (for which the same basis set will apply for
                all species) or a dict specifying basis sets on a per
                atom basis. E.g., {"C": "6-311++G**",
                "H": "6-31++G**"}. Defaults to "6-31++G**".
            theory_directives:
                A dict of theory directives. For example,
                if you are running dft calculations, you may specify the
                exchange correlation functional using {"xc": "b3lyp"}.
        """
        #Basic checks.
        if theory.lower() not in NwTask.theories.keys():
            raise NwInputError("Invalid theory {}".format(theory))

        if operation.lower() not in NwTask.operations.keys():
            raise NwInputError("Invalid operation {}".format(operation))

        self.mol = mol
        self.title = title if title is not None else "{} {} {}".format(
            re.sub("\s", "", mol.formula), theory, operation)

        self.charge = charge if charge is not None else mol.charge
        nelectrons = - self.charge + mol.charge + mol.nelectrons
        if spin_multiplicity is not None:
            self.spin_multiplicity = spin_multiplicity
            if (nelectrons + spin_multiplicity) % 2 != 1:
                raise ValueError(
                    "Charge of {} and spin multiplicity of {} is"
                    " not possible for this molecule".format(
                        charge, spin_multiplicity))
        else:
            self.spin_multiplicity = 1 if nelectrons % 2 == 0 else 2

        elements = set(mol.composition.get_el_amt_dict().keys())

        self.theory = theory
        if hasattr(basis_set, "items") and hasattr(basis_set, "keys"):
            if not elements.issubset(basis_set.keys()):
                raise NwInputError("Too few basis sets specified.")
            self.basis_set = basis_set
        else:
            self.basis_set = {el: basis_set for el in elements}

        self.elements = elements
        self.operation = operation
        self.theory_directives = theory_directives \
            if theory_directives is not None else {}

    def __str__(self):
        o = ["title \"{}\"".format(self.title),
             "charge {}".format(self.charge),
             "basis"]
        for el, bset in self.basis_set.items():
            o.append(" {} library \"{}\"".format(el, bset))
        o.append("end")
        if self.theory_directives:
            o.append("{}".format(self.theory))
            for k, v in self.theory_directives.items():
                o.append(" {} {}".format(k, v))
            o.append("end")
        o.append("task {} {}".format(self.theory, self.operation))
        return "\n".join(o)

    @property
    def to_dict(self):
        return {"@module": self.__class__.__module__,
                "@class": self.__class__.__name__, "mol": self.mol.to_dict,
                "charge": self.charge,
                "spin_multiplicity": self.spin_multiplicity,
                "title": self.title, "theory": self.theory,
                "operation": self.operation, "basis_set": self.basis_set,
                "theory_directives": self.theory_directives}

    @classmethod
    def from_dict(cls, d):
        mol = Molecule.from_dict(d["mol"])
        return NwTask(mol, charge=d["charge"],
                      spin_multiplicity=d["spin_multiplicity"],
                      title=d["title"], theory=d["theory"],
                      operation=d["operation"], basis_set=d["basis_set"],
                      theory_directives=d["theory_directives"])

    @classmethod
    def dft_task(cls, mol, xc="b3lyp", **kwargs):
        """
        A class method for quickly creating DFT tasks.

        Args:
            mol:
                Input molecule
            xc:
                Exchange correlation to use.
            \*\*kwargs:
                Any of the other kwargs supported by NwTask. Note the theory
                is always "dft" for a dft task.
        """
        t = NwTask(mol, **kwargs)
        t.theory = "dft"
        t.theory_directives.update({"xc": xc,
                                    "mult": t.spin_multiplicity})
        return t


class NwInput(MSONable):
    """
    An object representing a Nwchem input file, which is essentially a list
    of tasks on a particular molecule.
    """

    def __init__(self, mol, tasks, directives=None):
        """
        Args:
            mol:
                Input molecule. If molecule is a single string, it is used as a
                direct input to the geometry section of the Gaussian input
                file.
            tasks:
                List of NwTasks.
            directives:
                List of root level directives as tuple. E.g.,
                [("start", "water"), ("print", "high")]
        """
        self._mol = mol
        if directives is None:
            self.directives = [("start", re.sub("\s", "", self._mol.formula))]
        else:
            self.directives = directives
        self.tasks = tasks

    @property
    def molecule(self):
        """
        Returns molecule associated with this GaussianInput.
        """
        return self._mol

    def __str__(self):
        o = []
        for d in self.directives:
            o.append("{} {}".format(d[0], d[1]))
        o.append("geometry units angstroms")
        for site in self._mol:
            o.append(" {} {} {} {}".format(site.specie.symbol, site.x, site.y,
                                           site.z))
        o.append("end\n")
        for t in self.tasks:
            o.append(str(t))
            o.append("")
        return "\n".join(o)

    def write_file(self, filename):
        with zopen(filename, "w") as f:
            f.write(self.__str__())

    @property
    def to_dict(self):
        return {
            "mol": self._mol.to_dict,
            "tasks": [t.to_dict for t in self.tasks],
            "directives": [list(t) for t in self.directives]
        }

    @classmethod
    def from_dict(cls, d):
        return NwInput(Molecule.from_dict(d["mol"]),
                       [NwTask.from_dict(dt) for dt in d["tasks"]],
                       [tuple(li) for li in d["directives"]])

    @classmethod
    def from_string(cls, string_input):
        """
        Read an NwInput from a string. Currently tested to work with
        files generated from this class itself.

        Args:
            string_input:
                string_input to parse.

        Returns:
            NwInput object
        """
        chunks = re.split("\n\s*\n", string_input.strip())
        directives = []
        species = []
        coords = []
        read_geom = False
        for l in chunks.pop(0).split("\n"):
            if read_geom:
                if l.strip().lower() == "end":
                    mol = Molecule(species, coords)
                    read_geom = False
                else:
                    toks = l.strip().split()
                    species.append(toks[0])
                    coords.append(map(float, toks[1:]))
            elif not l.strip().startswith("geometry"):
                directives.append(l.strip().split())
            else:
                read_geom = True

        for c in chunks:
            charge = None
            spin_multiplicity = None
            title = None
            theory = "scf"
            operation = None
            basis_set = {}
            for l in c.strip().split("\n"):
                toks = l.strip().split()
                if toks[0] == "charge":
                    charge = int(toks[1])
                    """
            (self, mol, charge=None, spin_multiplicity=None,
                 title=None, theory="dft", operation="optimize",
                 basis_set="6-31++G**", theory_directives=None)
                    """
        return NwInput(mol, tasks=[], directives=directives)

    @classmethod
    def from_file(cls, filename):
        """
        Read an NwInput from a file. Currently tested to work with
        files generated from this class itself.

        Args:
            filename:
                Filename to parse.

        Returns:
            NwInput object
        """
        with zopen(filename) as f:
            return cls.from_string(f.read())


class NwInputError(Exception):
    """
    Error class for NwInput.
    """
    pass


class NwOutput(object):
    """
    A Nwchem output file parser. Very basic for now - supports only dft and
    only parses energies and geometries. Please note that Nwchem typically
    outputs energies in either au or kJ/mol. All energies are converted to
    eV in the parser.
    """

    def __init__(self, filename):
        self.filename = filename

        with zopen(filename) as f:
            data = f.read()

        chunks = re.split("NWChem Input Module", data)
        if re.search("CITATION", chunks[-1]):
            chunks.pop()
        preamble = chunks.pop(0)
        self.job_info = self._parse_preamble(preamble)
        self.data = map(self._parse_job, chunks)

    def _parse_preamble(self, preamble):
        info = {}
        for l in preamble.split("\n"):
            toks = l.split("=")
            if len(toks) > 1:
                info[toks[0].strip()] = toks[-1].strip()
        return info

    def _parse_job(self, output):
        energy_patt = re.compile("Total \w+ energy\s+=\s+([\.\-\d]+)")
        coord_patt = re.compile("\d+\s+(\w+)\s+[\.\-\d]+\s+([\.\-\d]+)\s+"
                                "([\.\-\d]+)\s+([\.\-\d]+)")
        corrections_patt = re.compile("([\w\-]+ correction to \w+)\s+="
                                      "\s+([\.\-\d]+)")
        preamble_patt = re.compile("(No. of atoms|No. of electrons"
                                   "|SCF calculation type|Charge|Spin "
                                   "multiplicity)\s*:\s*(\S+)")
        error_defs = {"Calculation failed to converge": "Bad convergence",
                      "geom_binvr: #indep variables incorrect": "autoz error"}

        data = {}
        energies = []
        corrections = {}
        molecules = []
        species = []
        coords = []
        errors = []
        basis_set = {}
        parse_geom = False
        parse_bset = False
        job_type = ""
        for l in output.split("\n"):
            for e, v in error_defs.items():
                if l.find(e) != -1:
                    errors.append(v)
            if parse_geom:
                if l.strip() == "Atomic Mass":
                    molecules.append(Molecule(species, coords))
                    species = []
                    coords = []
                    parse_geom = False
                else:
                    m = coord_patt.search(l)
                    if m:
                        species.append(m.group(1).capitalize())
                        coords.append([float(m.group(2)), float(m.group(3)),
                                       float(m.group(4))])
            elif parse_bset:
                if l.strip() == "":
                    parse_bset = False
                else:
                    toks = l.split()
                    if toks[0] != "Tag" and not re.match("\-+", toks[0]):
                        basis_set[toks[0]] = dict(zip(bset_header[1:],
                                                      toks[1:]))
                    elif toks[0] == "Tag":
                        bset_header = toks
                        bset_header.pop(4)
                        bset_header = [h.lower() for h in bset_header]
            else:
                m = energy_patt.search(l)
                if m:
                    energies.append(float(m.group(1)) * phyc.Ha_eV)
                    continue

                m = preamble_patt.search(l)
                if m:
                    try:
                        val = int(m.group(2))
                    except ValueError:
                        val = m.group(2)
                    k = m.group(1).replace("No. of ", "n").replace(" ", "_")
                    data[k.lower()] = val
                elif l.find("Geometry \"geometry\"") != -1:
                    parse_geom = True
                elif l.find("Summary of \"ao basis\"") != -1:
                    parse_bset = True
                elif job_type == "" and l.strip().startswith("NWChem"):
                    job_type = l.strip()
                else:
                    m = corrections_patt.search(l)
                    if m:
                        corrections[m.group(1)] = float(m.group(2)) / \
                            phyc.EV_PER_ATOM_TO_KJ_PER_MOL

        data.update({"job_type": job_type, "energies": energies,
                     "corrections": corrections,
                     "molecules": molecules,
                     "basis_set": basis_set,
                     "errors": errors,
                     "has_error": len(errors) > 0})

        return data