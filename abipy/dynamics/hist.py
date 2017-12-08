# coding: utf-8
"""History file with structural relaxation results."""
from __future__ import print_function, division, unicode_literals, absolute_import

import os
import numpy as np
import pymatgen.core.units as units

from collections import OrderedDict
from monty.functools import lazy_property
from monty.collections import AttrDict
from monty.string import marquee # is_string, list_strings
from pymatgen.core.periodic_table import Element
from abipy.tools.plotting import add_fig_kwargs, get_ax_fig_plt, get_axarray_fig_plt
from abipy.core.structure import Structure
from abipy.core.mixins import AbinitNcFile, NotebookWriter
from abipy.abio.robots import Robot
from abipy.iotools import ETSF_Reader


class HistFile(AbinitNcFile, NotebookWriter):
    """
    File with the history of a structural relaxation or molecular dynamics calculation.

    Usage example:

    .. code-block:: python

        with HistFile("foo_HIST") as hist:
            hist.plot()
    """
    @classmethod
    def from_file(cls, filepath):
        """Initialize the object from a Netcdf file"""
        return cls(filepath)

    def __init__(self, filepath):
        super(HistFile, self).__init__(filepath)
        self.reader = HistReader(filepath)

    def close(self):
        self.reader.close()

    def __str__(self):
        return self.to_string()

    @lazy_property
    def final_energy(self):
        return self.etotals[-1]

    @lazy_property
    def final_pressure(self):
        """Final pressure in Gpa."""
        cart_stress_tensors, pressures = self.reader.read_cart_stress_tensors()
        return pressures[-1]

    #@lazy_property
    #def final_max_force(self):

    #def get_fstats_dict(self, step):
    #    #for step in range(self.num_steps):
    #    forces_hist = self.reader.read_cart_forces()
    #    fmin_steps, fmax_steps, fmean_steps, fstd_steps = [], [], [], []

    #    forces = forces_hist[step]
    #    fmods = np.sqrt([np.dot(force, force) for force in forces])
    #    fmean_steps.append(fmods.mean())
    #    fstd_steps.append(fmods.std())
    #    fmin_steps.append(fmods.min())
    #    fmax_steps.append(fmods.max())

    def to_string(self, verbose=0, title=None):
        """Return string representation."""
        lines = []; app = lines.append
        if title is not None: app(marquee(title, mark="="))

        app(marquee("File Info", mark="="))
        app(self.filestat(as_string=True))
        app("")
        app(self.initial_structure.to_string(verbose=verbose, title="Initial Structure"))
        app("")
        app("Number of relaxation steps performed: %d" % self.num_steps)
        app(self.final_structure.to_string(verbose=verbose, title="Final structure"))
        app("")

        an = self.get_relaxation_analyzer()
        app("Volume change in percentage: %.2f%%" % (an.get_percentage_volume_change() * 100))
        d = an.get_percentage_lattice_parameter_changes()
        vals = tuple(d[k] * 100 for k in ("a", "b", "c"))
        app("Percentage lattice parameter changes:\n\ta: %.2f%%, b: %.2f%%, c: %2.f%%" % vals)
        #an.get_percentage_bond_dist_changes(max_radius=3.0)
        app("")

        cart_stress_tensors, pressures = self.reader.read_cart_stress_tensors()
        app("Stress tensor (Cartesian coordinates in Ha/Bohr**3):\n%s" % cart_stress_tensors[-1])
        app("Pressure: %.3f [GPa]" % pressures[-1])

        return "\n".join(lines)

    @property
    def num_steps(self):
        """Number of iterations performed."""
        return self.reader.num_steps

    @lazy_property
    def steps(self):
        """step indices."""
        return list(range(self.num_steps))

    @property
    def initial_structure(self):
        """The initial structure."""
        return self.structures[0]

    @property
    def final_structure(self):
        """The structure of the last iteration."""
        return self.structures[-1]

    @lazy_property
    def structures(self):
        """List of :class:`Structure` objects at the different steps."""
        return self.reader.read_all_structures()

    @lazy_property
    def etotals(self):
        """numpy array with total energies in eV at the different steps."""
        return self.reader.read_eterms().etotals

    def get_relaxation_analyzer(self):
        """
        Return a pymatgen :class:`RelaxationAnalyzer` object to analyze the relaxation in a calculation.
        """
        from pymatgen.analysis.structure_analyzer import RelaxationAnalyzer
        return RelaxationAnalyzer(self.initial_structure, self.final_structure)

    def to_xdatcar(self, filepath=None, groupby_type=True, **kwargs):
        """
        Return Xdatcar pymatgen object. See write_xdatcar for the meaning of arguments.

        Args:
            kwargs: keywords arguments passed to Xdatcar constructor.
        """
        filepath = self.write_xdatcar(filepath=filepath, groupby_type=groupby_type, overwrite=True)
        from pymatgen.io.vasp.outputs import Xdatcar
        return Xdatcar(filepath, **kwargs)

    def write_xdatcar(self, filepath="XDATCAR", groupby_type=True, overwrite=False):
        """
        Write Xdatcar file with unit cell and atomic positions to file `filepath`.

        Args:
            filepath: Xdatcar filename. If None, a temporary file is created.
            groupby_type: If True, atoms are grouped by type. Note that this option
                may change the order of the atoms. This option is needed because
                there are post-processing tools (e.g. ovito) that do not work as expected
                if the atoms in the structure are not grouped by type.
            overwrite: raise RuntimeError, if False and filepath exists.

        Return:
            path to Xdatcar file.
        """
        if filepath is not None and os.path.exists(filepath) and not overwrite:
            raise RuntimeError("Cannot overwrite pre-existing file `%s`" % filepath)
        if filepath is None:
            import tempfile
            fd, filepath = tempfile.mkstemp(text=True)

        # int typat[natom], double znucl[npsp]
        typat = self.reader.read_value("typat")
        znucl = self.reader.read_value("znucl")
        if len(typat) != len(znucl):
            raise RuntimeError("Alchemical mixing is not supported.")
        symb2pos = OrderedDict()
        symbols_atom = []
        for iatom, itype in enumerate(typat):
            itype = itype - 1
            symbol = Element.from_Z(int(znucl[itype])).symbol
            if symbol not in symb2pos: symb2pos[symbol] = []
            symb2pos[symbol].append(iatom)
            symbols_atom.append(symbol)

        if not groupby_type:
            group_ids = np.arange(self.reader.natom)
        else:
            group_ids = []
            for pos_list in symb2pos.values():
                group_ids.extend(pos_list)
            group_ids = np.array(group_ids, dtype=np.int)

        comment = " %s\n" % self.initial_structure.formula
        with open(filepath, "wt") as fh:
            # comment line  + scaling factor set to 1.0
            fh.write(comment)
            fh.write("1.0\n")
            for vec in self.initial_structure.lattice.matrix:
                fh.write("%.12f %.12f %.12f\n" % (vec[0], vec[1], vec[2]))
            if not groupby_type:
                fh.write(" ".join(symbols_atom) + "\n")
                fh.write("1 " * len(symbols_atom) + "\n")
            else:
                fh.write(" ".join(symb2pos.keys()) + "\n")
                fh.write(" ".join(str(len(p)) for p in symb2pos.values()) + "\n")

            # Write atomic positions in reduced coordinates.
            xred_list = self.reader.read_value("xred")
            for step in range(self.num_steps):
                fh.write("Direct configuration= %d\n" % (step + 1))
                frac_coords = xred_list[step, group_ids]
                for fs in frac_coords:
                    fh.write("%.12f %.12f %.12f\n" % (fs[0], fs[1], fs[2]))

        return filepath

    def plot_ax(self, ax, what, **kwargs):
        """
        Helper function to plot quantity `what` on axis `ax`.
        kwargs are passed to matplotlib plot method
        """
        if what == "energy":
            # Total energy in eV.
            ax.plot(self.steps, self.etotals, label="Energy", **kwargs)
            ax.set_ylabel('Total energy [eV]')
            ax.set_xlabel('Step')

        elif what == "abc":
            # Lattice parameters.
            for i, label in enumerate(["a", "b", "c"]):
                ax.plot(self.steps, [s.lattice.abc[i] for s in self.structures], label=label, **kwargs)
            ax.set_ylabel('Lattice lengths [A]')
            ax.legend(loc='best', shadow=True)

        elif what == "angles":
            # Lattice Angles
            for i, label in enumerate(["alpha", "beta", "gamma"]):
                ax.plot(self.steps, [s.lattice.angles[i] for s in self.structures], label=label, **kwargs)
            ax.set_ylabel('Lattice Angles [degree]')
            ax.legend(loc='best', shadow=True)

        elif what == "volume":
            ax.plot(self.steps, [s.lattice.volume for s in self.structures], **kwargs)
            ax.set_ylabel('Lattice volume [A^3]')

        elif what == "pressure":
            stress_cart_tensors, pressures = self.reader.read_cart_stress_tensors()
            ax.plot(self.steps, pressures, label="Pressure", **kwargs)
            ax.set_ylabel('Pressure [GPa]')

        elif what == "forces":
            forces_hist = self.reader.read_cart_forces()
            fmin_steps, fmax_steps, fmean_steps, fstd_steps = [], [], [], []
            for step in range(self.num_steps):
                forces = forces_hist[step]
                fmods = np.sqrt([np.dot(force, force) for force in forces])
                fmean_steps.append(fmods.mean())
                fstd_steps.append(fmods.std())
                fmin_steps.append(fmods.min())
                fmax_steps.append(fmods.max())

            ax.plot(self.steps, fmin_steps, label="min |F|", **kwargs)
            ax.plot(self.steps, fmax_steps, label="max |F|", **kwargs)
            ax.plot(self.steps, fmean_steps, label="mean |F|", **kwargs)
            ax.plot(self.steps, fstd_steps, label="std |F|", **kwargs)
            ax.set_ylabel('Force stats [eV/A]')
            ax.legend(loc='best', shadow=True)
            ax.set_xlabel('Step')

        else:
            raise ValueError("Invalid value for what: `%s`" % what)

    @add_fig_kwargs
    def plot(self, axlist=None, **kwargs):
        """
        Plot the evolution of structural parameters (lattice lengths, angles and volume)
        as well as pressure, info on forces and total energy.

        Args:
            axlist: List of matplotlib Axes. If None, a new figure is created.

        Returns:
            `matplotlib` figure
        """
        import matplotlib.pyplot as plt
        fig, ax_list = plt.subplots(nrows=3, ncols=2, sharex=True, squeeze=False)
        ax_list = ax_list.ravel()
        ax0, ax1, ax2, ax3, ax4, ax5 = ax_list
        for ax in ax_list: ax.grid(True)

        # Lattice parameters.
        self.plot_ax(ax0, "abc", marker="o")
        # Lattice Angles
        self.plot_ax(ax1, "angles", marker="o")
        # Lattice volume
        self.plot_ax(ax2, "volume", marker="o")
        # Pressure
        self.plot_ax(ax3, "pressure", marker="o")
        # Forces
        self.plot_ax(ax4, "forces", marker="o")
        # Total energy.
        self.plot_ax(ax5, "energy", marker="o")

        return fig

    @add_fig_kwargs
    def plot_energies(self, ax=None, **kwargs):
        """
        Plot the total energies as function of the iteration step.

        Args:
            ax: matplotlib :class:`Axes` or None if a new figure should be created.

        Returns:
            `matplotlib` figure
        """
        # TODO max force and pressure
        ax, fig, plt = get_ax_fig_plt(ax=ax)

        terms = self.reader.read_eterms()
        for key, values in terms.items():
            if np.all(values == 0.0): continue
            ax.plot(self.steps, values, marker="o", label=key)

        ax.set_xlabel('Step')
        ax.set_ylabel('Energies [eV]')
        ax.grid(True)
        ax.legend(loc='best', shadow=True)

        return fig

    def mvplot_trajectories(self, colormap="hot", sampling=1, figure=None, show=True, with_forces=True, **kwargs):
        """
        Call mayavi to plot atomic trajectories and the variation of the unit cell.
        """
        from abipy.display import mvtk
        figure, mlab = mvtk.get_fig_mlab(figure=figure)
        style = "labels"
        line_width = 100
        mvtk.plot_structure(self.initial_structure, style=style, unit_cell_color=(1, 0, 0), figure=figure)
        mvtk.plot_structure(self.final_structure, style=style, unit_cell_color=(0, 0, 0), figure=figure)

        steps = np.arange(start=0, stop=self.num_steps, step=sampling)
        xcart_list = self.reader.read_value("xcart") * units.bohr_to_ang
        for iatom in range(self.reader.natom):
            x, y, z = xcart_list[::sampling, iatom, :].T
            #for i in zip(x, y, z): print(i)
            trajectory = mlab.plot3d(x, y, z, steps, colormap=colormap, tube_radius=None,
                                    line_width=line_width, figure=figure)
            mlab.colorbar(trajectory, title='Iteration', orientation='vertical')

        if with_forces:
            fcart_list = self.reader.read_cart_forces(unit="eV ang^-1")
            for iatom in range(self.reader.natom):
                x, y, z = xcart_list[::sampling, iatom, :].T
                u, v, w = fcart_list[::sampling, iatom, :].T
                q = mlab.quiver3d(x, y, z, u, v, w, figure=figure, colormap=colormap,
                                  line_width=line_width, scale_factor=10)
                #mlab.colorbar(q, title='Forces [eV/Ang]', orientation='vertical')

        if show: mlab.show()
        return figure

    def mvanimate(self, delay=500):
        from abipy.display import mvtk
        figure, mlab = mvtk.get_fig_mlab(figure=None)
        style = "points"
        #mvtk.plot_structure(self.initial_structure, style=style, figure=figure)
        #mvtk.plot_structure(self.final_structure, style=style, figure=figure)

        xcart_list = self.reader.read_value("xcart") * units.bohr_to_ang
        #t = np.arange(self.num_steps)
        #line_width = 2
        #for iatom in range(self.reader.natom):
        #    x, y, z = xcart_list[:, iatom, :].T
        #    trajectory = mlab.plot3d(x, y, z, t, colormap=colormap, tube_radius=None, line_width=line_width, figure=figure)
        #mlab.colorbar(trajectory, title='Iteration', orientation='vertical')

        #x, y, z = xcart_list[0, :, :].T
        #nodes = mlab.points3d(x, y, z)
        #nodes.glyph.scale_mode = 'scale_by_vector'
        #this sets the vectors to be a 3x5000 vector showing some random scalars
        #nodes.mlab_source.dataset.point_data.vectors = np.tile( np.random.random((5000,)), (3,1))
        #nodes.mlab_source.dataset.point_data.scalars = np.random.random((5000,))

        @mlab.show
        @mlab.animate(delay=delay, ui=True)
        def anim():
            """Animate."""
            for it, structure in enumerate(self.structures):
            #for it in range(self.num_steps):
                print('Updating scene for iteration:', it)
                #mlab.clf(figure=figure)
                mvtk.plot_structure(structure, style=style, figure=figure)
                #x, y, z = xcart_list[it, :, :].T
                #nodes.mlab_source.set(x=x, y=y, z=z)
                #figure.scene.render()
                mlab.draw(figure=figure)
                yield

        anim()

    def write_notebook(self, nbpath=None):
        """
        Write an ipython notebook to nbpath. If nbpath is None, a temporay file in the current
        working directory is created. Return path to the notebook.
        """
        nbformat, nbv, nb = self.get_nbformat_nbv_nb(title=None)

        nb.cells.extend([
            #nbv.new_markdown_cell("# This is a markdown cell"),
            nbv.new_code_cell("hist = abilab.abiopen('%s')" % self.filepath),
            nbv.new_code_cell("print(hist)"),
            nbv.new_code_cell("hist.plot_energies();"),
            nbv.new_code_cell("hist.plot();"),
        ])

        return self._write_nb_nbpath(nb, nbpath)


class HistRobot(Robot):
    """
    This robot analyzes the results contained in multiple HIST files.
    """
    EXT = "HIST"

    def to_string(self, verbose=0):
        """String representation with verbosity level `verbose`."""
        s = ""
        if verbose:
            s = super(HistRobot, self).to_string(verbose=0)
        df = self.get_dataframe()
        s_df = "Table with final structures, pressures in GPa and force stats in eV/Ang:\n\n%s" % str(df)
        if s:
            return "\n".join([s, str(s_df)])
        else:
            return str(s_df)

    def get_dataframe(self, with_geo=True, index=None, abspath=False, with_spglib=True, funcs=None, **kwargs):
        """
        Return a pandas DataFrame with the most important final results.
        and the filenames as index.

        Args:
            with_geo: True if structure info should be added to the dataframe
            abspath: True if paths in index should be absolute. Default: Relative to getcwd().
            index: Index of the dataframe, if None, robot labels are used
            with_spglib: If True, spglib is invoked to get the spacegroup symbol and number

        kwargs:
            attrs:
                List of additional attributes of the :class:`GsrFile` to add to
                the pandas :class:`DataFrame`
            funcs: Function or list of functions to execute to add more data to the DataFrame.
                Each function receives a :class:`GsrFile` object and returns a tuple (key, value)
                where key is a string with the name of column and value is the value to be inserted.
        """
        # Add attributes specified by the users
        # TODO add more columns
        attrs = [
            "num_steps", "final_energy", "final_pressure",
            #"final_min_force", "final_max_force",
            #"ecut", "pawecutdg", "tsmear", "nkpt", "nsppol", "nspinor", "nspden",
        ] + kwargs.pop("attrs", [])

        rows, row_names = [], []
        for label, hist in self:
            row_names.append(label)
            d = OrderedDict()

            #fstas_dict = self.get_fstats_dict(step=-1)

            # Add info on structure.
            if with_geo:
                d.update(hist.final_structure.get_dict4pandas(with_spglib=with_spglib))

            for aname in attrs:
                if aname in ("final_min_force", "final_max_force"):
                    value = fstas_dict[aname]
                else:
                    value = getattr(hist, aname, None)
                d[aname] = value

            # Execute functions
            if funcs is not None: d.update(self._exec_funcs(funcs, hist))
            rows.append(d)

        import pandas as pd
        row_names = row_names if not abspath else self._to_relpaths(row_names)
        index = row_names if index is None else index
        return pd.DataFrame(rows, index=index, columns=list(rows[0].keys()))

    @property
    def what_list(self):
        """List with all quantities that can be plotted (what argument)."""
        return ["energy", "abc", "angles", "volume", "pressure", "forces"]

    @add_fig_kwargs
    def gridplot(self, what="abc", sharex=False, sharey=False, **kwargs):
        """
        Plot multiple HIST files on a grid.

        Args:
            what: Quantity to plot. Must be in ["energy", "abc", "angles", "volume", "pressure", "forces"]
            sharex: True if xaxis should be shared.
            sharey: True if yaxis should be shared.

        Returns:
            matplotlib figure.
        """
        num_plots, ncols, nrows = len(self), 1, 1
        if num_plots > 1:
            ncols = 2
            nrows = (num_plots//ncols) + (num_plots % ncols)

        ax_list, fig, plt = get_axarray_fig_plt(None, nrows=nrows, ncols=ncols,
                                                sharex=sharex, sharey=sharey, squeeze=False)
        ax_list = ax_list.ravel()

        for i, (ax, hist) in enumerate(zip(ax_list, self.ncfiles)):
            hist.plot_ax(ax, what, marker="o")
            ax.set_title(hist.relpath)
            ax.grid(True)
            if i == len(ax_list) - 1:
                ax.set_xlabel('Step')

        # Get around a bug in matplotlib.
        if num_plots % ncols != 0:
            ax_list[-1].plot([0, 1], [0, 1], lw=0)
            ax_list[-1].axis('off')

        fig.tight_layout()
        return fig

    def write_notebook(self, nbpath=None):
        """
        Write a jupyter notebook to nbpath. If nbpath is None, a temporay file in the current
        working directory is created. Return path to the notebook.
        """
        nbformat, nbv, nb = self.get_nbformat_nbv_nb(title=None)

        args = [(l, f.filepath) for l, f in self.items()]
        nb.cells.extend([
            #nbv.new_markdown_cell("# This is a markdown cell"),
            nbv.new_code_cell("robot = abilab.HistRobot(*%s)\nrobot.trim_paths()\nrobot" % str(args)),
            nbv.new_code_cell("df = robot.get_dataframe()\ndisplay(df)"),
        ])

        # Mixins
        #nb.cells.extend(self.get_baserobot_code_cells())

        return self._write_nb_nbpath(nb, nbpath)


class HistReader(ETSF_Reader):
    """This object reads data from the HIST file."""

    @lazy_property
    def num_steps(self):
        """Number of iterations present in the HIST file."""
        return self.read_dimvalue("time")

    @lazy_property
    def natom(self):
        """Number of atoms un the unit cell"""
        return self.read_dimvalue("natom")

    def read_all_structures(self):
        """Return the list of structures at the different iteration steps."""
        rprimd_list = self.read_value("rprimd")
        xred_list = self.read_value("xred")

        # Alchemical mixing is not supported.
        num_pseudos = self.read_dimvalue("npsp")
        ntypat = self.read_dimvalue("ntypat")
        if num_pseudos != ntypat:
            raise NotImplementedError("Alchemical mixing is not supported, num_pseudos != ntypat")

        znucl, typat = self.read_value("znucl"), self.read_value("typat")
        #print(znucl.dtype, typat)
        cart_forces_step = self.read_cart_forces(unit="eV ang^-1")

        structures = []
        #print("typat", type(typat))
        for step in range(self.num_steps):
            s = Structure.from_abivars(
                xred=xred_list[step],
                rprim=rprimd_list[step],
                acell=3 * [1.0],
                # FIXME ntypat, typat, znucl are missing!
                znucl=znucl,
                typat=typat,
            )
            s.add_site_property("cartesian_forces", cart_forces_step[step])
            structures.append(s)

        return structures

    def read_eterms(self, unit="eV"):
        return AttrDict(
            etotals=units.EnergyArray(self.read_value("etotal"), "Ha").to(unit),
            kinetic_terms=units.EnergyArray(self.read_value("ekin"), "Ha").to(unit),
            entropies=units.EnergyArray(self.read_value("entropy"), "Ha").to(unit),
        )

    def read_cart_forces(self, unit="eV ang^-1"):
        """
        Read and return a numpy array with the cartesian forces in unit `unit`.
        Shape (num_steps, natom, 3)
        """
        return units.ArrayWithUnit(self.read_value("fcart"), "Ha bohr^-1").to(unit)

    def read_reduced_forces(self):
        """
        Read and return a numpy array with the forces in reduced coordinates
        Shape (num_steps, natom, 3)
        """
        return self.read_value("fred")

    def read_cart_stress_tensors(self):
        """
        Return the stress tensors (nstep x 3 x 3) in cartesian coordinates (Hartree/Bohr^3)
        and the list of pressures in GPa unit.
        """
        # Abinit stores 6 unique components of this symmetric 3x3 tensor:
        # Given in order (1,1), (2,2), (3,3), (3,2), (3,1), (2,1).
        c = self.read_value("strten")
        tensors = np.empty((self.num_steps, 3, 3), dtype=np.float)

        for step in range(self.num_steps):
            for i in range(3): tensors[step, i,i] = c[step, i]
            for p, (i, j) in enumerate(((2,1), (2,0), (1,0))):
                tensors[step, i,j] = c[step, 3+p]
                tensors[step, j,i] = c[step, 3+p]

        HaBohr3_GPa = 29421.033 # 1 Ha/Bohr^3, in GPa
        pressures = np.empty(self.num_steps)
        for step, tensor in enumerate(tensors):
            pressures[step] = - (HaBohr3_GPa/3) * tensor.trace()

        return tensors, pressures
