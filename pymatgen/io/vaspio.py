#!/usr/bin/env python
from __future__ import division
"""
Classes for reading/manipulating/writing VASP files.
"""

__author__="Shyue Ping Ong"
__copyright__ = "Copyright 2011, The Materials Project"
__version__ = "1.1"
__maintainer__ = "Shyue Ping Ong"
__email__ = "shyue@mit.edu"
__status__ = "Production"
__date__ ="$Sep 23, 2011M$"

import os
import re
import itertools
import warnings
import xml.sax.handler
import StringIO
from collections import defaultdict
import ConfigParser

import numpy as np
from numpy import identity, array, zeros
from numpy.linalg import det

import pymatgen
from pymatgen.io.io_abc import VaspInput
from pymatgen.util.string_utils import str_aligned, str_delimited
from pymatgen.util.io_utils import file_open_zip_aware, clean_lines, micro_pyawk, clean_json
from pymatgen.core.structure import Structure, Composition
from pymatgen.core.periodic_table import Element
from pymatgen.core.electronic_structure import CompleteDos, Dos, PDos, Spin, Orbital
from pymatgen.core.lattice import Lattice


class Poscar(VaspInput):
    """
    Object for representing the data in a POSCAR or CONTCAR file.
    """
    
    def __init__(self, struct, comment = None):
        """
        Creates a POSCAR file from a Structure object.
        Arguments:
            struct - Structure object. See pymatgen.core.structure.Structure.
            comment - Optional comment line for POSCAR. Defaults to unit cell formula of structure.
        """

        if struct.is_ordered:
            self._struct = struct
            self._site_symbols = []
            self._natoms = []
            curr_sym = struct[0].specie.symbol
            curr_amt = 0
            for site in struct:
                el = site.specie
                if el.symbol == curr_sym:
                    curr_amt += 1
                else:
                    self._site_symbols.append(curr_sym)
                    self._natoms.append(curr_amt)
                    curr_sym = el.symbol
                    curr_amt = 1
            self._site_symbols.append(curr_sym)
            self._natoms.append(curr_amt)
            self._true_names = True
            self._selective_dynamics = None
            self.comment = struct.formula if comment == None else comment    
        else:
            raise ValueError("Structure with partial occupancies cannot be converted into POSCAR!")

    @property
    def site_symbols(self):
        """
        Symbols for each site in POSCAR.
        """
        return self._site_symbols
    
    @property
    def struct(self):
        """
        Structure associated with the Poscar file.
        """
        return self._struct

    @staticmethod
    def from_file(filename):
        """
        Reads a Poscar from a file.
        The code will try its best to determine the elements in the POSCAR in the following order:
            i) Ideally, if the input file is Vasp5-like and contains element symbols in the 6th line, the code will use that.
            ii) Failing (i), the code will check if a symbol is provided at the end of each coordinate.
            iii) Failing (i) and (ii), the code will try to check if a POTCAR is in the same directory as the POSCAR and use elements from that.
        If all else fails, the code will just assign the first n elements in increasing atomic number, where n is the number of species, 
        to the Poscar.  For example, H, He, Li, ....  This will ensure at least a unique element is assigned to each site and any analysis 
        that does not require specific elemental properties should work fine.
        Arguments:
            filename - file name containing Poscar data.
        Returns:
            Poscar object.
        """
        
        with file_open_zip_aware(filename, "r") as f:
            lines = tuple(clean_lines(f.readlines(), False))

        comment = lines[0]
        scale = float(lines[1])
        lattice = identity(3, float)
        lattice[0] = array([float(s) for s in lines[2].split()])
        lattice[1] = array([float(s) for s in lines[3].split()])
        lattice[2] = array([float(s) for s in lines[4].split()])
        if scale < 0:
            vol = abs(det(lattice))
            lattice = (-scale / vol) ** (1. / 3.) * lattice
        else:
            lattice = scale * lattice
        lattice = Lattice(lattice)

        found_symbols = False
        try:
            natoms = [int(s) for s in lines[5].split()]
        except:
            found_symbols = True

        #Checking for Vasp5+ style Poscars
        if found_symbols:
            symbols = lines[5].split()
            natoms = [int(s) for s in lines[6].split()]
            atomic_symbols = list()
            for i in xrange(len(natoms)):
                atomic_symbols.extend([symbols[i]] * natoms[i])
            ipos = 7
        else:
            ipos = 6

        postype = lines[ipos].split()[0]
        cart = False
        # Selective dynamics
        if postype[0] in 'sS':
            ipos += 1
            postype = lines[ipos].split()[0]
        if postype[0] in 'cCkK':
            cart = True
        N = sum(natoms)

        # See if the element names are appended at the end.
        if not found_symbols:
            try:
                atomic_symbols = [l.split()[3] for l in lines[ipos + 1:ipos + 1 + N]]
                count = 0
                names = list()
                for num in natoms:
                    if not Element.is_valid_symbol(atomic_symbols[count]):
                            raise ValueError("Invalid name")
                    names.append(atomic_symbols[count])
                    count += num
                found_symbols = True
            except:
                pass
            
        #Try to find a element from a POTCAR in the same directory.
        if not found_symbols:
            dirname = os.path.dirname(os.path.abspath(filename))
            for f in os.listdir(dirname):
                if re.search("POTCAR.*",f):
                    try:
                        warnings.warn("POTCAR found! Using elements from POTCAR.")
                        potcar = Potcar.from_file(os.path.join(dirname, f))
                        names = [sym.split("_")[0] for sym in potcar.symbols]
                        atomic_symbols = list()
                        for i in xrange(len(natoms)):
                            atomic_symbols.extend([names[i]] * natoms[i])
                        found_symbols = True
                    except Exception as ex:
                        pass
        
        #Defaulting to false names.
        if not found_symbols:
            names = list()
            atomic_symbols = list()
            for i in xrange(len(natoms)):
                sym = Element.from_Z(i+1).symbol
                names.append(sym)
                atomic_symbols.extend([sym] * natoms[i])
            warnings.warn("Elements in POSCAR cannot be determined. Defaulting to false names, " + " ".join(names)+".")

        # read the atomic coordinates
        coords = zeros((N, 3), float)
        for i in xrange(N):
            iL = ipos + 1 + i
            coords[i] = array([float(s) for s in lines[iL].split()[:3]], float)
            
        struct = Structure(lattice, atomic_symbols, coords, False, False, cart)

        return Poscar(struct,comment)
    
    def get_string(self, direct = True, vasp4_compatible = False):
        """
        Returns a string to be written as a
        POSCAR file. Site symbols are written
        which means compatibilty is for vasp >= 5.
        Optional arguments:
            direct - Whether coordinates are output in direct or cartesian.
            vasp4_compatible - Set to True to omit site symbols on 6th line to maintain backward vasp 4.x compatibility.
        """
        lines = []
        lines += [self.comment]
        lines += ["1.0"]
        lines += [str(self._struct.lattice)]
        if self._true_names == True and not vasp4_compatible:
            lines += [" ".join(self._site_symbols)]
        lines += [" ".join([str(x) for x in self._natoms])]
        if self._selective_dynamics != None:
            lines += ["Selective dynamics"]
            #extra = map(lambda sdrow: " " + ("F", "T")[sdrow[0]] + " " + ("F", "T")[sdrow[1]] + " " + ("F", "T")[sdrow[2]], self._selective_dynamics)
        #else:
        #    extra = None
        if direct == True:
            lines += ["direct"]
            lines += ["%.6f %.6f %.6f %s" % (site.a, site.b, site.c, site.species_string) for site in self._struct.sites]  
        else:
            lines += ["cartesian"]
            lines += ["%.6f %.6f %.6f %s" % (site.x, site.y, site.z, site.species_string) for site in self._struct.sites]

        return "\n".join(lines)    

    def __str__(self):
        """
        String representation of Poscar file.
        """
        return self.get_string()

    def set_site_symbols(self, symbols):
        self._site_symbols = symbols
        self._true_names = True
        #update the Structure as well
        elements = list()
        for i in range(len(symbols)):
            elements.extend([symbols[i]]*self._natoms[i])
        self._struct = Structure(self._struct.lattice, elements, self._struct.frac_coords)

    def write_file(self, filename):
        with open(filename, 'w') as f:
            f.write(str(self) + "\n")
        
VALID_INCAR_TAGS = ("NGX", "NGY", "NGZ", "NGXF", "NGYF", "NGZF", "NBANDS", "NBLK", "SYSTEM", "NWRITE", "ENCUT", "ENAUG",
"PREC", "ISPIN", "MAGMOM", "ISTART", "ICHARG", "INIWAV", "NELM", "NELMIN", "NELMDL", "EDIFF", "EDIFFG", "NSW", "NBLOCK",
"KBLOCK", "IBRION", "NFREE", "POTIM", "ISIF", "PSTRESS", "IWAVPR", "ISYM", "SYMPREC", "LCORR", "TEBEG", "TEEND", "SMASS",
"NPACO", "APACO", "POMASS", "ZVAL", "RWIGS", "LORBIT", "NELECT", "NUPDOWN", "EMIN", "EMAX", "NEDOS", "ISMEAR", "SIGMA", 
"FERWE", "FERDO", "SMEARINGS", "LREAL", "ROPT", "GGA", "VOSKOWN", "LASPH", "ALGO", "IALGO", "LDIAG", "NSIM", "IMIX", "INIMIX", 
"MAXMIX", "AMIX", "BMIX", "AMIX_MAG", "BMIX_MAG", "AMIN", "MIXPRE", "WC", "WEIMIN", "EBREAK", "DEPER", "TIME", "LWAVE", "LCHARG",
"LVTOT", "LELF", "NPAR", "LPLANE","LASYNC", "LSCALAPACK", "LSCALU", "ISPIND", "HFSCREEN", "LHFCALC", "ENCUTFOCK", "NKRED", "LMAXMIX",
"PRECFOCK", "AEXX", "AGGAX", "AGGAC", "ALDAC", "LMAXFOCK", "LMAXFOCKAE", "LTHOMAS", "NKREDX", "NKREDY", "NKREDZ", "EVENONLY", "ODDONLY", "LDAU", "LDAUJ", "LDAUL", "LDAUPRINT", "LDAUTYPE", "LDAUU", "LPEAD", "LCALCPOL", "LCALCEPS", "LEFG", "EFIELD_PEAD", "LNONCOLLINEAR",
"LSORBIT", "IDIPOL", "DIPOL", "LMONO", "LDIPOL", "EPSILON", "EFIELD", "LBERRY", "IGPAR", "NPPSTR", "IPEAD", "I_CONSTRAINED_M", "LAMBDA", "M_CONSTR",
"IMAGES", "SPRING", "LOPTICS", "CSHIFT", "LNABLA", "LEPSILON", "LRPA", "NOMEGA", "NOMEGAR", "LSPECTRAL", "OMEGAMAX", "OMEGATL", "ENCUTGW",
"ENCUTGWSOFT", "ODDONLYGW", "EVENONLYGW", "LSELFENERGY", 'LRHFATM', 'METAGGA', 'LMAXTAU', 'LCOMPAT','ENMAX', 'LMAXPAW', 'LSPIRAL', 'LZEROZ',
'LMETAGGA','ENINI', 'NRMM', 'MREMOVE', 'ADDGRID', 'EFERMI', 'LPARD', 'LSCAAWARE', 'IDIOT', 'LMUSIC', 'LREAL_COMPAT', 'GGA_COMPAT', 'ICORELEVEL', 'LHFONE',
'LRHFCALC', 'LMODELHF', 'ENCUT4O', 'EXXOEP', 'FOURORBIT', 'HFALPHA', 'ALDAX', 'SHIFTRED', 'NMAXFOCKAE', 'HFSCREENC', 'MODEL_GW', 'MODEL_EPS0', 'MODEL_ALPHA',
'LVEL', 'SAXIS', 'QSPIRAL', 'STM', 'KINTER', 'ORBITALMAG', 'LMAGBLOCH', 'LCHIMAG', 'LGAUGE', 'MAGATOM', 'MAGDIPOL', 'AVECCONST', 'LTCTE', 'LTETE',
'L2ORDER', 'LGWLF', 'ENCUTLF', 'LMAXMP2', 'SCISSOR', 'NBANDSGW', 'NBANDSLF', 'DIM', 'ANTIRES', 'LUSEW', 'OMEGAGRID', 'SELFENERGY', 'NKREDLFX', 'NKREDLFY',
'NKREDLFZ', 'MAXMEM', 'TELESCOPE', 'LCRITICAL_MEM', 'GGA2',
'TURBO', 'QUAD_EFG','IRESTART','NREBOOT','NMIN','EREF','KSPACING','KGAMMA','LSUBROT','SCALEE','LVHAR','LORBITALREAL','DARWINR','DARWINV','LFOCKAEDFT','NUCIND','MAGPOS','LNICSALL','LADDER','LHARTREE','IBSE','NBANDSO','NBANDSV','OPTEMAX')

class Incar(dict, VaspInput):
    """
    INCAR object for reading and writing INCAR files
    essentially consists of a dictionary with some helper functions
    """
    
    def __init__(self,params = dict()):
        """
        Creates an Incar object.
        Optional arguments:
            params - A set of input parameters as a dictionary.
        """
        super(Incar,self).__init__()
        self.update(params)

    def __setitem__(self, key, val):
        """
        Add parameter-val pair to Incar.  Warns if parameter is not in list of valid INCAR tags.
        Also cleans the parameter and val by stripping leading and trailing white spaces.
        """
        if key.strip().upper() not in VALID_INCAR_TAGS:
            warnings.warn(key.strip() + " not in VALID_INCAR_TAGS")
        super(Incar,self).__setitem__(key.strip(), Incar.proc_val(key.strip(), val.strip()) if isinstance(val,basestring) else val)

    def get_string(self, sort_keys = False, pretty = False):
        keys = self.keys()
        if sort_keys:
            keys = sorted(keys)
        lines = []
        for k in keys:
            if isinstance(self[k], list):
                lines.append([k," ".join([str(i) for i in self[k]])])
            else:
                lines.append([k,self[k]])
        
        if pretty:
            return str_aligned(lines)
        else:
            return str_delimited(lines, None," = ")
    
    def __str__(self):
        return self.get_string(sort_keys = True, pretty = False)

    def write_file(self, filename):
        with open(filename, 'w') as f:
            f.write(self.__str__() + "\n")
        
    @staticmethod
    def from_file(filename):
        with file_open_zip_aware(filename, "r") as f:
            lines = list(clean_lines(f.readlines()))
        params = {}
        for line in lines:
            m = re.match("(\w+)\s*=\s*(.*)", line)
            if m:
                key = m.group(1).strip()
                val = m.group(2).strip()
                val = Incar.proc_val(key, val)
                params[key] = val
        return Incar(params)
    
    @staticmethod
    def proc_val(key, val):
        list_type_keys = ('LDAUU', 'LDAUL', 'LDAUJ', 'LDAUTYPE','MAGMOM')
        boolean_type_keys = ('LDAU', 'LWAVE')
        number_type_keys = ('NSW', 'NELMIN', 'ISIF', 'IBRION', "ISPIN", "EDIFF", "ICHARG", "NELM", "ISMEAR", "NPAR", "SIGMA", "LDAUPRINT", 'LMAXMIX')
         
        
        def smart_int_or_float(numstr):
            if numstr.find(".") != -1:
                return float(numstr)
            else:
                return int(numstr)
        try:
            if key in list_type_keys:
                output = list()
                toks = re.split("\s+", val)
                
                for tok in toks:
                    m = re.match("(\d+)\*([\d\.\-\+]+)", tok)
                    if m:
                        output.extend([smart_int_or_float(m.group(2))] * int(m.group(1)))
                    else:
                        output.append(smart_int_or_float(tok))
                return output
            if key in boolean_type_keys:
                m = re.search("^\W+([TtFf])", val)
                if m:
                    if m.group(1) == "T" or m.group(1) == "t":
                        return True
                    else:
                        return False
                raise ValueError(key + " should be a boolean type!")
            
            if key in number_type_keys:
                return smart_int_or_float(val)
        except:
            return val
              
        return val
            
    def diff(self, other):
        similar_param = {}
        different_param = {}
        for k1,v1 in self.items():
            if k1 not in other:
                different_param[k1] = {"INCAR1": v1, "INCAR2": 'Default'}
            elif v1 != other[k1]:
                different_param[k1] = {"INCAR1": v1, "INCAR2": other[k1]}
            else:
                similar_param[k1] = v1
        for k2,v2 in other.items():
            if k2 not in similar_param and k2 not in different_param:
                if k1 not in self:
                    different_param[k1] = {"INCAR1": 'Default', "INCAR2": v2}
        return {'Same parameters' : similar_param, 'Different': different_param}
                
    def __add__(self, other):
        """
        Add all the values of another INCAR object to this object
        Facilitates the use of "standard" INCARs
        """
        params = {k:v for k,v in self.items()}
        for k, v in other.items():
            if k in self and v != self[k]:
                raise ValueError("Incars have conflicting values!")
            else:
                params[k] = v
        return Incar(params)

class Kpoints(VaspInput):
    """
    Very basic KPOINT reader/writer
    The lines of the original file
    are stored as is but the kpts are stored
    as a list which can be accessed and
    modified
    """
    def __init__(self):
        self.l1_comment = "Default"
        self.l2_gen_style = "0"
        self.l3_lattice = "Gamma"
        self.kpts = [1,1,1]

    @staticmethod
    def from_file(filename):
        with file_open_zip_aware(filename) as f:
            lines = list(clean_lines(f.readlines(), False))
        kpoints = Kpoints()
        kpoints.l1_comment = lines[0].strip()
        kpoints.l2_gen_style = lines[1].strip()
        kpoints.l3_lattice = lines[2].strip()
        kpoints.kpts = [int(x) for x in lines[3].strip().split()]
        return kpoints

    def write_file(self, filename):
        with open(filename, 'w') as f:
            f.write(self.__str__() + "\n")
        
    def __str__(self):
        lines = []
        lines += [self.l1_comment]
        lines += [self.l2_gen_style]
        lines += [self.l3_lattice]
        lines += [" ".join([str(x) for x in self.kpts])]
        return "\n".join(lines)
    
    @property
    def to_dict(self):
        return {'comment': self.l1_comment, 'generation style' : self.l2_gen_style, 'lattice' : self.l3_lattice, 'mesh': self.kpts}
    
class PotcarSingle(VaspInput):
    """
    Object for a **single** POTCAR.
    The builder assumes the complete string is the POTCAR
    contains the complete untouched data in "data" as a string and
    a dictionary of keywords
    """
    def __init__(self, data):
        """
        Expects a complete and single potcar file as a string in "data"
        """
        self.data = data # raw POTCAR as a string
        keyValPairs = re.compile(r";*\s*(.+?)\s*=\s*([^;\n]+)\s*", re.M).findall(data)
        self.keywords = dict(keyValPairs) # all key = val found in the POTCAR as a dictionary all keys and vals are strings

    def __str__(self):
        return self.data

    def write_file(self, filename):
        writer = open(filename, 'w')
        writer.write(self.__str__() + "\n")
        writer.close()

    @property
    def symbol(self):
        """
        Full name of POTCAR, e.g., Fe_pv
        """
        return self.keywords['TITEL'].split(" ")[1].strip()        
    
    @property
    def element(self):
        """
        Attempt to return the atomic symbol based on the VRHFIN keyword
        """
        return self.keywords['VRHFIN'].split(":")[0].strip()

    @property
    def atomic_no(self):
        """
        Attempt to return the atomic number based on the VRHFIN keyword
        """
        return Element(self.element).Z


DEFAULT_POTCAR_CHOICES = {"Li":"Li_sv",
"O":"O",
"Na":"Na_pv",
"K":"K_sv",
"Cs":"Cs_sv",
"Rb":"Rb_sv",
"Be":"Be_sv",
"Mg":"Mg_pv",
"Ca":"Ca_sv",
"Sr":"Sr_sv",
"Ba":"Ba_sv",
"Sc":"Sc_sv",
"Y":"Y_sv",
"Ti":"Ti_pv",
"Zr":"Zr_sv",
"Hf":"Hf_pv",
"V":"V_sv",
"Nb":"Nb_pv",
"Ta":"Ta_pv",
"Cr":"Cr_pv",
"Mo":"Mo_pv",
"W":"W_pv",
"Mn":"Mn_pv",
"Tc":"Tc_pv",
"Re":"Re_pv",
"Fe":"Fe_pv",
"Co":"Co",
"Ni":"Ni_pv",
"Cu":"Cu_pv",
"Zn":"Zn",
"Ru":"Ru_pv",
"Rh":"Rh_pv",
"Pd":"Pd",
"Ag":"Ag",
"Cd":"Cd",
"Hg":"Hg",
"Au":"Au",
"Ir":"Ir",
"Pt":"Pt",
"Os":"Os_pv",
"Ga":"Ga_d",
"Ge":"Ge_d",
"Al":"Al",
"As":"As",
"Se":"Se",
"Br":"Br",
"In":"In_d",
"Sn":"Sn_d",
"Tl":"Tl_d",
"Pb":"Pb_d",
"Bi":"Bi",
"Po":None,
"At":None,
"La":"La",
"Ce":"Ce",
"Pr":"Pr_3",
"Nd":"Nd_3",
"Pm":"Pm_3",
"Sm":"Sm_3",
"Eu":"Eu",
"Gd":"Gd",
"Tb":"Tb_3",
"Dy":"Dy_3",
"Ho":"Ho_3",
"Er":"Er_3",
"Tm":"Tm_3",
"Yb":"Yb",
"Lu":"Lu_3",
"P":"P",
"H" : "H",
"Pu" : "Pu",
"C": "C",
"Pa" : "Pa",
"Xe" : "Xe",
"He" : "He",
"S" : "S",
"Ne" : "Ne",
"Np" : "Np",
"Fr" : None,
"Fm" : None,
"B" : "B",
"F" : "F",
"N" : "N",
"Kr": "Kr",
"Si" : "Si",
"Sb": "Sb",
"Cm": None,
"Cl": "Cl",
"Cf": None,
"Lr" : None,
"Th" : "Th",
"Te" : "Te",
"I" : "I",
"U" : "U",
"Ac" : "Ac",
"Am" : None,
"Ar" : "Ar",
"Ra" : None,
"Rn" : None,
"Bk" : None,
"Md" : None,
"Es" : None,
"No" : None
}




class Potcar(list,VaspInput):
    """
    Object for reading and writing POTCAR files for
    calculations.
    """
    functional_dir = {'PBE':'POT_GGA_PAW_PBE', 'LDA':'POT_LDA_PAW', 'PW91':'POT_GGA_PAW_PW91'}
    
    def __init__(self, symbols = None):
        if symbols != None:
            self.set_symbols(symbols)

    @staticmethod
    def from_file(filename):
        reader = file_open_zip_aware(filename, "r")
        fData = reader.read()
        reader.close()
        potcar = Potcar()
        potcar_strings = re.compile(r"\n{0,1}\s*(.*?End of Dataset)", re.S).findall(fData)
        for p in potcar_strings:
            potcar.append(PotcarSingle(p))
        return potcar

    def __str__(self):
        return "\n".join([str(potcar) for potcar in self])

    def write_file(self, filename):
        with open(filename, 'w') as f:
            f.write(self.__str__() + "\n")
        
    @property
    def symbols(self):
        """
        Get the atomic symbols of all the atoms in the POTCAR file
        """
        return [p.symbol for p in self]

    def set_symbols(self, elements, functional = 'PBE', use_element_default = True):
        module_dir = os.path.dirname(pymatgen.__file__)
        config = ConfigParser.SafeConfigParser()
        config.readfp(open(os.path.join(module_dir, "pymatgen.cfg")))
        VASP_PSP_DIR = os.path.join(config.get('VASP', 'pspdir'), Potcar.functional_dir[functional])
        
        del self[:]
        for el in elements:
            sym = el
            if Element.is_valid_symbol(el) and use_element_default and el in DEFAULT_POTCAR_CHOICES:
                sym = DEFAULT_POTCAR_CHOICES[el]
            reader = file_open_zip_aware(os.path.join(VASP_PSP_DIR, "POTCAR." + sym + ".gz"))
            self.append(PotcarSingle(reader.read()))
            reader.close()

class Vasprun(object):
    """
    Vastly improved sax-based parser for vasprun.xml files.
    Speedup over Dom is at least 2x for smallish files (~1Mb) to orders of magnitude for larger files (~10Mb).
    All data is stored as attributes, which are delegated to the VasprunHandler object.
    Accessible attributes from VasprunHandler are:
        Vasp results
        ------------
        energies - All energies in run, represented as a list of ionic steps, with a list of scstep energies.
                   E.g. [ [0.1, 0.2, 0.1], [0.1], [0.22, 0.23, 0.20, 0.205]] 
        structures - List of Structure objects for the structure at each ionic step.
        tdos - Total dos calculated at the end of run.
        idos - Integrated dos calculated at the end of run.
        pdos - List of list of PDos objects. Access as pdos[atomindex][orbitalindex]
        efermi - Fermi energy
        eigenvalues - Final eigenvalues as a dict of {(kpoint index, Spin.up):[[eigenvalue, occu]]}. 
                      This representation is probably not ideal, but since this is not used anywhere else for now, I leave it as such.
                      Future developers who need to work with this should refactored the object into a sensible structure.
        
        Vasp inputs
        -----------
        incar - Incar object for parameters specified in INCAR file.
        parameters - Incar object with parameters that vasp actually used, including all defaults.
        kpoints = Kpoints object for KPOINTS specified in run.
        actual_kpoints - List of actual kpoints, e.g., [[0.25, 0.125, 0.08333333], [-0.25, 0.125, 0.08333333], [0.25, 0.375, 0.08333333], ....]
        actual_kpoints_weights = List of kpoint weights, E.g., [0.04166667, 0.04166667, 0.04166667, 0.04166667, 0.04166667, 0.04166667, ....]
        atomic_symbols - List of atomic symbols, e.g., [u'Li', u'Fe', u'Fe', u'P', u'P', u'P']
        potcar_symbols - List of POTCAR symbols. E.g., [u'PAW_PBE Li 17Jan2003', u'PAW_PBE Fe 06Sep2000', ..]
    
    A few helper attributes have also been added to get commonly used results such as final energies.
    
    Author: Shyue Ping Ong
    """
    supported_properties = ['vasp_version', 'incar', 'parameters', 'potcar_symbols', 'atomic_symbols', 'kpoints', 'actual_kpoints', 'structures',
                            'actual_kpoints_weights', 'dos_energies', 'eigenvalues', 'tdos', 'idos', 'pdos', 'efermi', 'ionic_steps']
    
    def __init__(self, filename):
        self._filename = filename    
        with file_open_zip_aware(filename) as f:
            self._handler = VasprunHandler(filename)
            self._parser = xml.sax.parse(f, self._handler)
            for k in Vasprun.supported_properties:
                setattr(self, k, getattr(self._handler, k))
    
    @property
    def converged(self):
        return len(self.structures) - 2 < self.parameters['NSW'] or self.parameters['NSW'] == 0
    
    @property
    def final_energy(self):
        """
        Final energy from the vasp run.
        """
        return self.ionic_steps[-1]['electronic_steps'][-1]['e_wo_entrp']
    
    @property
    def final_structure(self):
        """
        Final structure from vasprun.
        """
        return self.structures[-1]
    
    @property
    def initial_structure(self):
        """
        Initial structure from vasprun.
        """
        return self.structures[0]
    
    @property
    def complete_dos(self):
        """
        A complete dos object which incorporates the total dos and all projected dos.
        """
        return CompleteDos(self.final_structure, self.tdos, self.pdos)
    
    @property
    def to_dict(self):
        d = {}
        d['vasp_version'] = self.vasp_version
        d['has_vasp_completed'] = self.converged
        d['nsites'] = len(self.final_structure)
        d['unit_cell_formula'] = self.final_structure.composition.to_dict
        comp = self.final_structure.composition
        d['reduced_cell_formula'] = Composition.from_formula(comp.reduced_formula).to_dict
        d['pretty_formula'] = comp.reduced_formula
        symbols = [re.split("\s+", s)[1] for s in self.potcar_symbols]
        symbols = [re.split("_", s)[0] for s in symbols]
        d['elements'] = symbols
        d['nelements'] = len(symbols)
        d['is_hubbard'] = self.incar.get('LDAU', False)
        if d['is_hubbard']:
            us = self.incar['LDAUU']
            js = self.incar['LDAUJ']
            d['hubbards'] = { symbols[i] : us[i] - js[i] for i in xrange(len(symbols))}
        else:
            d['hubbards'] = {}
        if d['is_hubbard']:
            d['run_type'] = "GGA+U"
        elif self.parameters.get('LHFCALC', False):
            d['run_type'] = "HF"
        else:
            d['run_type'] = "GGA"
                   
        d['input'] = {}
        d['input']['incar'] = {k:v for k,v in self.incar.items()}
        d['input']['crystal'] = self.initial_structure.to_dict
        kpts = self.kpoints
        d['input']['kpoints'] = {'comment': kpts.l1_comment, 'generation style' : kpts.l2_gen_style, 'lattice' : kpts.l3_lattice, 'mesh': kpts.kpts}
        d['input']['kpoints']['actual_points'] = [{'abc':list(self.actual_kpoints[i]), 'weight':self.actual_kpoints_weights[i]} for i in xrange(len(self.actual_kpoints))] 
        d['input']['potcar'] = [s.split(" ")[1] for s in self.potcar_symbols]
        d['input']['parameters'] = {k:v for k,v in self.parameters.items()}
        
        d['output'] = {}
        d['output']['ionic_steps'] = clean_json(self.ionic_steps)
        d['output']['final_energy'] = self.final_energy
        d['output']['final_energy_per_atom'] = self.final_energy / len(self.final_structure)
        d['output']['crystal'] = self.final_structure.to_dict
        d['output']['efermi'] = self.efermi
        #{(kpoint index, Spin.up):array(float)}
        
        d['output']['eigenvalues'] = {}
        for (index, spin), values in self.eigenvalues.items():
            if str(index) not in d['output']['eigenvalues']:
                d['output']['eigenvalues'][str(index)] = {str(spin):values}
            else:
                d['output']['eigenvalues'][str(index)][str(spin)] = values
                
        return d

 
class VasprunHandler(xml.sax.handler.ContentHandler):
    """
    Sax handler for vasprun.xml.
    Attributes are mirrored into Vasprun object.
    
    Author: Shyue Ping Ong
    """
        
    def __init__(self, filename):
        self.filename = filename
        # variables to be filled
        self.vasp_version = None
        self.incar = Incar()
        self.parameters = Incar()
        self.potcar_symbols = []
        self.atomic_symbols = []
        self.kpoints = Kpoints()
        self.actual_kpoints = []
        self.actual_kpoints_weights = []
        self.dos_energies = None
        self.eigenvalues = {}#  will  be  {(kpoint index, Spin.up):array(float)}
        self.tdos = {}
        self.idos = {}
        self.pdos = {}
        self.efermi = None 
        self.ionic_steps = [] # should be a list of dict
        self.structures = []
        
        self.input_read = False
        self.all_calculations_read = False
        self.read_structure = False
        self.read_calculation = False
        self.read_eigen = False
        self.read_dos= False
        self.in_efermi = False
        self.read_atoms = False
        self.read_lattice = False
        self.read_positions = False
        self.incar_param = None
        self.dos_energies_val = []
        self.dos_val = []
        self.idos_val = []
        self.raw_data = []
        
        self.state = defaultdict(bool)
        
    
    def in_all(self, xml_tags):
        return all([getattr(self, 'in_'+tag, None) for tag in xml_tags])
    
    def startElement(self, name, attributes):
        
        self.state[name] = True if 'name' not in attributes else attributes['name']
        self.read_val = False
        
        #Nested if loops makes reading much faster.
        if not self.input_read: #reading input parameters
            if (name == "i" or name == "v") and (self.state['incar'] or self.state['parameters']):
                self.incar_param = attributes['name']
                self.param_type = 'float' if 'type' not in attributes else attributes['type']
                self.read_val = True
            elif name == "v" and self.state['kpoints']:
                self.read_val = True
            elif name == "generation" and self.state['kpoints']:
                self.kpoints.l1_comment   = "K point data read from vasprun.xml"
                self.kpoints.l2_gen_style  = "-1"
                self.kpoints.l3_lattice = attributes['param']
            elif name == "c" and (self.state['array'] == "atoms" or self.state['array'] == "atomtypes"):
                self.read_val = True
            elif name == "i" and self.state['i'] == "version" and self.state['generator']:
                self.read_val = True
                
        else: #reading calculations and structures.
            if self.read_calculation:
                if name == "i" and self.state['scstep']:
                    self.read_val = True
                elif name == "v" and (self.state['varray'] == "forces" or self.state['varray'] == "stress"):
                    self.read_positions = True
            if self.read_structure:
                if name == "v" and self.state['varray'] == 'basis':
                    self.read_lattice = True
                elif name == "v" and self.state['varray'] == 'positions':
                    self.read_positions = True
            if name == "calculation":
                self.scdata = []
                self.read_calculation = True
            elif name == "scstep":
                self.scstep = {}
            elif name == 'structure':
                self.latticestr = StringIO.StringIO()
                self.posstr = StringIO.StringIO()
                self.read_structure = True
            elif name == 'varray' and (self.state['varray'] == "forces" or self.state['varray'] == "stress"):
                self.posstr = StringIO.StringIO()

            elif name == "eigenvalues":
                self.all_calculations_read = True
            if self.read_eigen:
                if name == "r" and self.state["set"]:
                    self.read_val = True
                elif name == "set" and "comment" in attributes:
                    comment = attributes["comment"]
                    self.state["set"] = comment
                    if comment.startswith("spin"):
                        self.eigen_spin = Spin.up if self.state["set"] == "spin 1" else Spin.down
                    if comment.startswith("kpoint"):
                        self.eigen_kpoint = int(comment.split(" ")[1])
            elif self.read_dos:
                if (name == "i" and self.state["i"] == "efermi") or (name == "r" and self.state["set"] ):
                    self.read_val = True
                elif name == "set" and "comment" in attributes:
                    comment = attributes["comment"]
                    self.state["set"] = comment
                    if self.state['partial']:
                        if comment.startswith("ion"):
                            self.pdos_ion = int(comment.split(" ")[1])
                        elif comment.startswith("spin"):
                            self.pdos_spin = Spin.up if self.state["set"] == "spin 1" else Spin.down
            elif name == "dos":
                self.dos_energies = None
                self.tdos = {}
                self.idos = {}
                self.pdos = {}
                self.efermi = None 
                self.read_dos = True
            elif name == "eigenvalues":
                self.eigenvalues = {}#  will  be  {(kpoint index, Spin.up):array(float)}
                self.read_eigen = True
            
        if self.read_val:
            self.val = StringIO.StringIO()

    def characters(self, data):
        if self.read_val:
            self.val.write(data)
        if self.read_lattice:
            self.latticestr.write(data)
        elif self.read_positions:
            self.posstr.write(data)
    
    #To correct for stupid vasp bug which names Xenon as X!!
    EL_MAPPINGS = {'X':'Xe'}
    
    def endElement(self, name):
        
        if not self.input_read:
            if name == "i":
                if self.state['incar']:
                    self.incar[self.incar_param] = parse_parameters(self.param_type, self.val.getvalue().strip())
                elif self.state['parameters']:
                    self.parameters[self.incar_param] = parse_parameters(self.param_type, self.val.getvalue().strip())
                elif self.state['generator'] and self.state["i"] == "version":
                    self.vasp_version = self.val.getvalue().strip()
                self.incar_param = None
            elif name == "set":
                if self.state['array'] == "atoms":
                    self.atomic_symbols = self.atomic_symbols[::2]
                    self.atomic_symbols = [sym if sym not in VasprunHandler.EL_MAPPINGS else VasprunHandler.EL_MAPPINGS[sym] for sym in self.atomic_symbols]
                elif self.state['array'] == "atomtypes":
                    self.potcar_symbols = self.potcar_symbols[4::5]
                    self.input_read = True
            elif name == "c":
                if self.state['array'] == "atoms":
                    self.atomic_symbols.append(self.val.getvalue().strip())
                elif self.state['array'] == "atomtypes":
                    self.potcar_symbols.append(self.val.getvalue().strip())
            elif name == "v":
                if self.state['incar']:
                    self.incar[self.incar_param] = parse_v_parameters(self.param_type, self.val.getvalue().strip(), self.filename, self.incar_param)
                    self.incar_param = None
                elif self.state['parameters']:
                    self.parameters[self.incar_param] = parse_v_parameters(self.param_type, self.val.getvalue().strip(), self.filename, self.incar_param)
                elif self.state['kpoints']:
                    if self.state['varray'] == 'kpointlist':
                        self.actual_kpoints.append([float(x) for x in re.split("\s+",self.val.getvalue().strip())])
                    if self.state['varray'] == 'weights':
                        self.actual_kpoints_weights.append(float(self.val.getvalue()))
                    if self.state['v'] == "divisions":
                        self.kpoints.kpts   = [int(x) for x in re.split("\s+",self.val.getvalue().strip())]
        else:
            if self.read_calculation:
                if name == "i" and self.state['scstep']:
                    self.scstep[self.state['i']] = float(self.val.getvalue())
                elif name == 'scstep':
                    self.scdata.append(self.scstep)
                elif name == 'varray' and self.state['varray'] == "forces":
                    self.forces = np.array([float(x) for x in re.split("\s+",self.posstr.getvalue().strip())])
                    self.forces.shape = (len(self.atomic_symbols), 3)
                elif name == 'varray' and self.state['varray'] == "stress":
                    self.stress = np.array([float(x) for x in re.split("\s+",self.posstr.getvalue().strip())])
                    self.stress.shape = (3, 3)
                elif name == "calculation":
                    self.ionic_steps.append({'electronic_steps':self.scdata, 'structure':self.structures[-1], 'forces': self.forces, 'stress':self.stress})
                    self.read_calculation = False
            if self.read_structure:
                if name == "v":
                    self.read_positions = False
                    self.read_lattice = False
                elif name == "structure":
                    self.lattice = np.array([float(x) for x in re.split("\s+",self.latticestr.getvalue().strip())])
                    self.lattice.shape = (3,3)
                    self.pos = np.array([float(x) for x in re.split("\s+",self.posstr.getvalue().strip())])
                    self.pos.shape = (len(self.atomic_symbols), 3)
                    self.structures.append(Structure(self.lattice, self.atomic_symbols, self.pos))
                    self.read_structure = False
            elif self.read_dos:
                if name == "i" and self.state["i"] == "efermi":
                    self.efermi = float(self.val.getvalue().strip())
                elif name == "r" and self.state["total"]  and str(self.state["set"]).startswith("spin"):
                    tok = re.split("\s+", self.val.getvalue().strip())
                    self.dos_energies_val.append(float(tok[0]))
                    self.dos_val.append(float(tok[1]))
                    self.idos_val.append(float(tok[2]))
                elif name == "r" and self.state["partial"]  and str(self.state["set"]).startswith("spin"):
                    tok = re.split("\s+", self.val.getvalue().strip())
                    self.raw_data.append([float(i) for i in tok[1:]])
                elif name == "set" and self.state["total"] and str(self.state["set"]).startswith("spin"):
                    spin = Spin.up if self.state["set"] == "spin 1" else Spin.down
                    self.tdos[spin] = self.dos_val
                    self.idos[spin] = self.dos_val
                    self.dos_energies = self.dos_energies_val
                    self.dos_energies_val = []
                    self.dos_val = []
                    self.idos_val = []
                elif name == "set" and self.state["partial"] and str(self.state["set"]).startswith("spin"):
                    spin = Spin.up if self.state["set"] == "spin 1" else Spin.down
                    self.norbitals = len(self.raw_data[0])
                    for i in xrange(self.norbitals):
                        self.pdos[(self.pdos_ion, i, spin)] = [row[i] for row in self.raw_data]
                    self.raw_data = []
                elif name == "partial":
                    all_pdos = []
                    natom = len(self.atomic_symbols)
                    for iatom in xrange(1,natom+1):
                        all_pdos.append(list())
                        for iorbital in xrange(self.norbitals):
                            updos = self.pdos[(iatom, iorbital, Spin.up)]
                            downdos = None if (iatom, iorbital, Spin.down) not in self.pdos else self.pdos[(iatom, iorbital, Spin.down)]
                            if downdos:
                                all_pdos[-1].append(PDos(self.efermi, self.dos_energies, {Spin.up:updos, Spin.down:downdos}, Orbital.from_vasp_index(iorbital)))
                            else:
                                all_pdos[-1].append(PDos(self.efermi, self.dos_energies, {Spin.up:updos}, Orbital.from_vasp_index(iorbital)))
                    self.pdos = all_pdos
                elif name == "total":
                    self.tdos = Dos(self.efermi, self.dos_energies, self.tdos)
                    self.idos = Dos(self.efermi, self.dos_energies, self.idos)
                elif name == "dos":
                    self.read_dos = False
            elif self.read_eigen:
                if name == "r" and str(self.state["set"]).startswith("kpoint"):
                    tok = re.split("\s+", self.val.getvalue().strip())
                    self.raw_data.append([float(i) for i in tok])
                elif name == "set" and str(self.state["set"]).startswith("kpoint"):
                    self.eigenvalues[(self.eigen_kpoint, self.eigen_spin)] = self.raw_data
                    self.raw_data = []
                elif name == "eigenvalues":
                    self.read_eigen = False
                
        self.state[name] = False
        
def parse_parameters(val_type, val):
    if val_type == "logical":
        return (val == "T")
    elif val_type == "int":
        return int(val)
    elif val_type == "string":
        return val.strip()
    else:
        return float(val)

def parse_v_parameters(val_type, val, filename, param_name):
    if val_type == "logical":
        val = [True if i == "T" else False for i in re.split("\s+", val)]
    elif val_type == "int":
        try:
            val = [int(i) for i in re.split("\s+", val)]
        except ValueError:
            # Fix for stupid error in vasprun sometimes which displays
            # LDAUL/J as 2****
            val = parse_from_incar(filename, param_name)
            if val == None:
                raise IOError("Error in parsing vasprun.xml")
    elif val_type == "string":
        val = [i for i in re.split("\s+", val)]
    else:
        try:
            val = [float(i) for i in re.split("\s+", val)]
        except ValueError:
            # Fix for stupid error in vasprun sometimes which displays
            # MAGMOM as 2****
            val = parse_from_incar(filename, param_name)
            if val == None:
                raise IOError("Error in parsing vasprun.xml")
    return val

def parse_from_incar(filename, key):
    dirname = os.path.dirname(filename)
    for f in os.listdir(dirname):
        if re.search("INCAR",f):
            warnings.warn("INCAR found. Using "+key+" from INCAR.")
            incar = Incar.from_file(os.path.join(dirname, f))
            if key in incar:
                return incar[key]
            else:
                return None
    return None


class Outcar(object):
    """
    Parser for data in OUTCAR that is not available in Vasprun.xml

    Note, this class works a bit differently than most of the other VaspObjects, since the OUTCAR can
    be very different depending on which "type of run" performed.

    Creating the OUTCAR class with a filename reads "regular parameters" that are always present.
    Presently these are just the magnetization, mag[ion] (magnetization per ion) and magtot (total magnetization)

    One can then call a specific reader depending on the type of run being perfromed. These are currently:
       readIGPAR()
       readLEPSILON()
       readLCALCPOL()

    See the documentation of those methods for more documentation.
    """
    def __init__(self, filename):
        self.mag = {} # dict of ion number to double
        self.filename = filename
        # read the data
        try:
            self.magmode = 0;
            search = []
            # Nonspin cases
            def magnetization_start(results, match): results.magmode = 1;
            search.append(['magnetization \(x\)', lambda results, line: results.magmode == 0, magnetization_start])

            def magnetization_tableline(results, match): results.magmode += 1
            search.append(['^\s*[-]+\s*$', lambda results, line: results.magmode > 0, magnetization_tableline])

            def magnetization_ion(results, match): results.mag[int(match.group(1))] = float(match.group(2));
            search.append([r'(\d+)\s+[-0-9\.]+\s+[-0-9\.]+\s+[-0-9\.]+\s+([-0-9\.]+)', lambda results, line: results.magmode == 2, magnetization_ion])

            def magnetization_total(results, match): results.totmag = float(match.group(1)); results.magmode = 0
            search.append([r'tot\s+[-0-9\.]+\s+[-0-9\.]+\s+[-0-9\.]+\s+([-0-9\.]+)', lambda results, line: results.magmode == 3, magnetization_total])

            micro_pyawk(self.filename, search, self)

        except RuntimeError:
            raise Exception("Error: magnetization not read")

    def get_magnetization(self):
        return self.mag

    def read_IGPAR(self):
        """ Renders accessible:
               er_ev = e<r>_ev (dictionary with Spin.up/Spin.down as keys)
               er_bp = e<r>_bp (dictionary with Spin.up/Spin.down as keys)
               er_ev_tot = spin up + spin down summed
               er_bp_tot = spin up + spin down summed
               p_elc = spin up + spin down summed
               p_ion = spin up + spin down summed
            (See VASP section 'LBERRY,  IGPAR,  NPPSTR,  DIPOL tags' for info on what these are)"""

        # variables to be filled
        self.er_ev = {}  #  will  be  dict (Spin.up/down) of array(3*float)
        self.er_bp = {}  #  will  be  dics (Spin.up/down) of array(3*float)
        self.er_ev_tot = None # will be array(3*float)
        self.er_bp_tot = None # will be array(3*float)
        self.p_elec = None
        self.p_ion = None
        try:
            search = []
            # Nonspin cases
            def er_ev(results, match): results.er_ev[Spin.up] = array([float(match.group(1)), float(match.group(2)), float(match.group(3))]) / 2.0; results.er_ev[Spin.down] = results.er_ev[Spin.up]; results.context = 2
            search.append(['^ *e<r>_ev=\( *([-0-9.Ee+]*) *([-0-9.Ee+]*) *([-0-9.Ee+]*) *\)', None, er_ev])

            def er_bp(results, match): results.er_bp[Spin.up] = array([float(match.group(1)), float(match.group(2)), float(match.group(3))]) / 2.0; results.er_bp[Spin.down] = results.er_bp[Spin.up]
            search.append(['^ *e<r>_bp=\( *([-0-9.Ee+]*) *([-0-9.Ee+]*) *([-0-9.Ee+]*) *\)', lambda results, line: results.context == 2, er_bp])

            # Spin cases
            def er_ev_up(results, match): results.er_ev[Spin.up] = array([float(match.group(1)), float(match.group(2)), float(match.group(3))]); results.context = Spin.up
            search.append(['^.*Spin component 1 *e<r>_ev=\( *([-0-9.Ee+]*) *([-0-9.Ee+]*) *([-0-9.Ee+]*) *\)', None, er_ev_up])

            def er_bp_up(results, match): results.er_bp[Spin.up] = array([float(match.group(1)), float(match.group(2)), float(match.group(3))])
            search.append(['^ *e<r>_bp=\( *([-0-9.Ee+]*) *([-0-9.Ee+]*) *([-0-9.Ee+]*) *\)', lambda results, line: results.context == Spin.up, er_bp_up])

            def er_ev_dn(results, match): results.er_ev[Spin.down] = array([float(match.group(1)), float(match.group(2)), float(match.group(3))]); results.context = Spin.down
            search.append(['^.*Spin component 2 *e<r>_ev=\( *([-0-9.Ee+]*) *([-0-9.Ee+]*) *([-0-9.Ee+]*) *\)', None, er_ev_dn])

            def er_bp_dn(results, match): results.er_bp[Spin.down] = array([float(match.group(1)), float(match.group(2)), float(match.group(3))])
            search.append(['^ *e<r>_bp=\( *([-0-9.Ee+]*) *([-0-9.Ee+]*) *([-0-9.Ee+]*) *\)', lambda results, line: results.context == Spin.down, er_bp_dn])

            # Always present spin/non-spin
            def p_elc(results, match): results.p_elc = array([float(match.group(1)), float(match.group(2)), float(match.group(3))])
            search.append(['^.*Total electronic dipole moment: *p\[elc\]=\( *([-0-9.Ee+]*) *([-0-9.Ee+]*) *([-0-9.Ee+]*) *\)', None, p_elc])

            def p_ion(results, match): results.p_ion = array([float(match.group(1)), float(match.group(2)), float(match.group(3))])
            search.append(['^.*ionic dipole moment: *p\[ion\]=\( *([-0-9.Ee+]*) *([-0-9.Ee+]*) *([-0-9.Ee+]*) *\)', None, p_ion])

            self.context = None
            self.er_ev = {Spin.up: None, Spin.down: None}
            self.er_bp = {Spin.up: None, Spin.down: None}

            micro_pyawk(self.filename, search, self)

            if self.er_ev[Spin.up] != None and self.er_ev[Spin.down] != None:
                self.er_ev_tot = self.er_ev[Spin.up] + self.er_ev[Spin.down]

            if self.er_bp[Spin.up] != None and self.er_bp[Spin.down] != None:
                self.er_bp_tot = self.er_bp[Spin.up] + self.er_bp[Spin.down]

        except:
            self.er_ev_tot = None
            self.er_bp_tot = None
            raise Exception("IGPAR OUTCAR could not be parsed.")

    def readLEPSILON(self):
        # variables to be filled
        try:
            search = []

            def dielectric_section_start(results, match): results.dielectric_index = -1;
            search.append(['MACROSCOPIC STATIC DIELECTRIC TENSOR', None, dielectric_section_start])

            def dielectric_section_start2(results, match): results.dielectric_index = 0
            search.append(['-------------------------------------', lambda results, line: results.dielectric_index == -1, dielectric_section_start2])

            def dielectric_data(results, match): results.dielectric_tensor[results.dielectric_index, :] = array([float(match.group(1)), float(match.group(2)), float(match.group(3))]); results.dielectric_index += 1
            search.append(['^ *([-0-9.Ee+]+) +([-0-9.Ee+]+) +([-0-9.Ee+]+) *$', lambda results, line: results.dielectric_index >= 0, dielectric_data])

            def dielectric_section_stop(results, match): results.dielectric_index = None
            search.append(['-------------------------------------', lambda results, line: results.dielectric_index >= 1, dielectric_section_stop])

            self.dielectric_index = None
            self.dielectric_tensor = zeros((3, 3))


            def piezo_section_start(results, match): results.piezo_index = 0;
            search.append(['PIEZOELECTRIC TENSOR  for field in x, y, z        \(e  Angst\)', None, piezo_section_start])

            def piezo_data(results, match): results.piezo_tensor[results.piezo_index, :] = array([float(match.group(1)), float(match.group(2)), float(match.group(3)), float(match.group(4)), float(match.group(5)), float(match.group(6))]); results.piezo_index += 1
            search.append(['^ *[xyz] +([-0-9.Ee+]+) +([-0-9.Ee+]+) +([-0-9.Ee+]+) *([-0-9.Ee+]+) +([-0-9.Ee+]+) +([-0-9.Ee+]+)*$', lambda results, line: results.piezo_index >= 0, piezo_data])

            def piezo_section_stop(results, match): results.piezo_index = None
            search.append(['-------------------------------------', lambda results, line: results.piezo_index >= 1, piezo_section_stop])

            self.piezo_index = None
            self.piezo_tensor = zeros((3, 6))


            def born_section_start(results, match): results.born_ion = -1;
            search.append(['BORN EFFECTIVE CHARGES \(in e, cummulative output\)', None, born_section_start])

            def born_ion(results, match): results.born_ion = int(match.group(1))-1; results.born[results.born_ion] = zeros((3, 3));
            search.append(['ion +([0-9]+)', lambda results, line: results.born_ion != None, born_ion])

            def born_data(results, match): results.born[results.born_ion][int(match.group(1))-1, :] = array([float(match.group(2)), float(match.group(3)), float(match.group(4))]);
            search.append(['^ *([1-3]+) +([-0-9.Ee+]+) +([-0-9.Ee+]+) +([-0-9.Ee+]+)$', lambda results, line: results.born_ion >= 0, born_data])

            def born_section_stop(results, match): results.born_index = None
            search.append(['-------------------------------------', lambda results, line: results.born_ion >= 1, born_section_stop])

            self.born_ion = None
            self.born = {}

            #def debug_print(results,match): print "MATCH:",match.group(0),':',results.born_ion
            #micro_pyawk(filename,search,self,debug=debug_print, postdebug=debug_print)

            micro_pyawk(self.filename, search, self)

        except:
            raise Exception("LEPSILON OUTCAR could not be parsed.")

    def readLCALCPOL(self):
        # variables to be filled
        self.p_elec = None
        self.p_ion = None
        try:
            search = []

            # Always present spin/non-spin
            def p_elc(results, match): results.p_elc = array([float(match.group(1)), float(match.group(2)), float(match.group(3))])
            search.append(['^.*Total electronic dipole moment: *p\[elc\]=\( *([-0-9.Ee+]*) *([-0-9.Ee+]*) *([-0-9.Ee+]*) *\)', None, p_elc])

            def p_ion(results, match): results.p_ion = array([float(match.group(1)), float(match.group(2)), float(match.group(3))])
            search.append(['^.*Ionic dipole moment: *p\[ion\]=\( *([-0-9.Ee+]*) *([-0-9.Ee+]*) *([-0-9.Ee+]*) *\)', None, p_ion])

            micro_pyawk(self.filename, search, self)

        except:
            raise Exception("CLACLCPOL OUTCAR could not be parsed.")

class VolumetricData(object):
    """
    Simple volumetric object for reading LOCPOT and CHGCAR type files
    """
    def __init__(self, filename):
        self.name   = str()
        self.poscar = None
        self.spinpolarized = False
        self.dim = None
        self.data = dict()
        self.numpts = 0
        self._read_file(filename)            

    def __add__(self, other):
        return self.linear_add(other, 1.0)

    def __sub__(self, other):
        return self.linear_add(other, -1.0)

    def linear_add(self, other, scalefactor=1.0):
        '''
        Method to do a linear sum of volumetric objects.  Use by + and - operators as well.
        '''
        #To add checks
        summed = VolumetricData()
        summed.name = self.name
        summed.poscar = self.poscar
        summed.spinpolarized = self.spinpolarized
        summed.dim = self.dim
        summed.numpts = self.numpts
        for spin in self.data.keys():
            summed.data[spin] = self.data[spin] + scalefactor* other.data[spin]
        return summed

    def get_num_gridpts(self):
        return self.numpts

    def _read_file(self, filename):

        reader = file_open_zip_aware(filename)
        lines = reader.readlines()
        reader.close()

        self.poscar = Poscar.from_file(filename)
        
        # Skip whitespace between POSCAR and LOCPOT data
        i = 0
        while lines[i].strip() != "":
            i += 1
        while lines[i].strip() == "":
            i += 1

        dimensionline = lines[i].strip()
        i += 1
        spinpolarized = False
        # Search for the second dimension line, where the next spin starts
        for j in xrange(i, len(lines)):
            if(dimensionline == lines[j].strip()):
                spinpolarized = True
                break

        if not spinpolarized:
            j = j + 2

        self.spinpolarized = spinpolarized

        # Read three numbers that is the dimension
        dimensionexpr = re.compile('([0-9]+) +([0-9]+) +([0-9]+)')
        m = dimensionexpr.match(dimensionline.strip())
        a = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        data = (" ".join(lines[i:j-1])).split()
        data = data[:(a[0] * a[1] * a[2])]

        self.dim = a
        self.numpts = self.dim[0] * self.dim[1] * self.dim[2]
        uppot = zeros((a[0], a[1], a[2]))
        count = 0
        for z in xrange(a[2]):
            for y in xrange(a[1]):
                for x in xrange(a[0]):
                    uppot[x, y, z] = float(data[count])
                    count += 1
        if spinpolarized:
            data = (" ".join(lines[j + 1:])).split()
            data = data[:(a[0] * a[1] * a[2])]
            downpot = zeros((a[0], a[1], a[2]))
            count = 0
            for (z,y,x) in itertools.product(xrange(a[2]), xrange(a[1]), xrange(a[0])):
                downpot[x, y, z] = float(data[count]) 
                count += 1
            self.data = {Spin.up:uppot, Spin.down:downpot}
        else:
            self.data = {Spin.up:uppot}


class Locpot(VolumetricData):
    """
    Simple object for reading a LOCPOT file
    """
    def __init__(self, filename):
        super(Locpot,self).__init__(filename)

    def get_avg_potential_along_axis(self, ind):
        """
        Get the averaged LOCPOT along a certain axis direction. Useful for visualizing Hartree Potentials
        """
        m = self.data[Spin.up]
        
        ng = self.dim
        avg = zeros((ng[ind], 1))
        for i in xrange(ng[ind]):
            mysum = 0
            for j in xrange(ng[(ind + 1) % 3]):
                for k in xrange(ng[(ind + 2) % 3]):
                    if ind == 0:
                        mysum += m[i,j,k]
                    if ind == 1:
                        mysum += m[k,i,j]
                    if ind == 2:
                        mysum += m[j,k,i]

            avg[i] = mysum / (ng[(ind + 1) % 3] * 1.0) / (ng[(ind + 2) % 3] * 1.0)
        return avg

class Chgcar(VolumetricData):
    """
    Simple object for reading a CHGCAR file
    """
    def __init__(self, filename):
        super(Chgcar,self).__init__(filename)
        #Chgcar format is total density in first set, and moment density in second set.
        # need to split them into up and down.
        updowndata = dict()
        updowndata[Spin.up] = 0.5 * (self.data[Spin.up] + self.data[Spin.down])
        updowndata[Spin.down] = 0.5 * (self.data[Spin.up] - self.data[Spin.down])
        self.data = updowndata
        self._distance_matrix = dict()

    def _calculate_distance_matrix(self, ind):
        structure = self.poscar.struct
        a = self.dim
        distances = dict()
        for (x,y,z) in itertools.product(xrange(a[0]), xrange(a[1]), xrange(a[2])):
            pt = array([x / a[0], y / a[1] ,z / a[2]])
            distances[(x,y,z)] = structure[ind].distance_and_image_from_frac_coords(pt)[0]
        self._distance_matrix[ind] = distances

    def get_diff_int_charge(self, ind, radius):
        if ind not in self._distance_matrix:
            self._calculate_distance_matrix(ind)
        a = self.dim
        intchg = 0
        for (x,y,z) in itertools.product(xrange(a[0]), xrange(a[1]), xrange(a[2])):
            if self._distance_matrix[ind][(x,y,z)] < radius:
                intchg += self.data[Spin.up][x, y, z] - self.data[Spin.down][x, y, z]
        return intchg / self.numpts
    
    def get_diff_int_charge_slow(self, ind, radius):
        st = self.poscar.struct
        a = self.dim
        intchg = 0
        ioncoord = st[ind].frac_coords
        iongridpt = [int(round(ioncoord[i]*a[i])) for i in xrange(3)]
        max_grid_pts = [min(int(round(radius/st.lattice.abc[i] * a[i]))+1,int(round(a[i]/2))) for i in xrange(3)]
        
        for x in xrange(iongridpt[0]-max_grid_pts[0],iongridpt[0]+max_grid_pts[0]):
            for y in xrange(iongridpt[1]-max_grid_pts[1],iongridpt[1]+max_grid_pts[1]):
                for z in xrange(iongridpt[2]-max_grid_pts[2],iongridpt[2]+max_grid_pts[2]):
                    modx = x % a[0]
                    mody = y % a[1]
                    modz = z % a[2]
                    pt = array([modx *1.0 / a[0], 1.0 * mody / a[1] , 1.0 * modz / a[2]])
                    dist = st[ind].distance_and_image_from_frac_coords(pt)[0]
                    if dist < radius:
                        intchg += self.data[Spin.up][modx, mody, modz] - self.data[Spin.down][modx, mody, modz]
        return intchg / self.numpts

class Procar(object):

    """
    Object for reading a PROCAR file
    """
    def __init__(self, filename):
        #create and return data object containing the information of a PROCAR type file
        self.name = ""
        self.data = dict()
        self._read_file(filename)

    def get_d_occupation(self, atomNo):
        row = self.data[atomNo]
        return sum(row[4:9])

    def _read_file(self, filename):
        reader = file_open_zip_aware(filename, "r")
        lines = clean_lines(reader.readlines())
        reader.close()
        self.name = lines[0]
        kpointexpr = re.compile("^\s*k-point\s+(\d+).*weight = ([0-9\.]+)")
        expr = re.compile('^\s*([0-9]+)\s+')
        dataexpr = re.compile('[\.0-9]+')
        currentKpoint = 0
        weight = 0
        for l in lines:
            if kpointexpr.match(l):
                m = kpointexpr.match(l)
                currentKpoint = int(m.group(1))
                weight = float(m.group(2))
                if currentKpoint == 1:
                    self.data = dict()
            if expr.match(l):
                linedata = dataexpr.findall(l)
                linefloatdata = map(float, linedata)
                index = int(linefloatdata.pop(0))
                if index in self.data:
                    self.data[index] = self.data[index] + array(linefloatdata) * weight
                else:
                    self.data[index] = array(linefloatdata) * weight


class VaspParserError(Exception):
    
    '''
    Exception class for Structure.
    Raised when the structure has problems, e.g., atoms that are too close.
    '''

    def __init__(self, msg):
        self.msg = msg

    def __str__(self):
        return "VaspParserError : " + self.msg
    