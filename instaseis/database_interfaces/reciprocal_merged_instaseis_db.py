#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Python library to extract seismograms from a set of wavefields generated by
AxiSEM.

:copyright:
    Martin van Driel (Martin@vanDriel.de), 2014
    Lion Krischer (krischer@geophysik.uni-muenchen.de), 2014
:license:
    GNU Lesser General Public License, Version 3 [non-commercial/academic use]
    (http://www.gnu.org/copyleft/lgpl.html)
"""
from __future__ import (absolute_import, division, print_function,
                        unicode_literals)

import collections
import numpy as np

from .base_netcdf_instaseis_db import BaseNetCDFInstaseisDB
from . import mesh
from .. import rotations, sem_derivatives, spectral_basis
from ..source import Source, ForceSource


class ReciprocalMergedInstaseisDB(BaseNetCDFInstaseisDB):
    """
    Reciprocal Merged Instaseis Database.
    """
    def __init__(self, db_path, netcdf_file, buffer_size_in_mb=100,
                 read_on_demand=False, *args, **kwargs):
        """
        :param db_path: Path to the Instaseis Database.
        :type db_path: str
        :param netcdf_file: The path to the actual netcdf4 file.
        :type netcdf_file: str
        :param buffer_size_in_mb: Strain and displacement are buffered to
            avoid repeated disc access. Depending on the type of database
            and the number of components of the database, the total buffer
            memory can be up to four times this number. The optimal value is
            highly application and system dependent.
        :type buffer_size_in_mb: int, optional
        :param read_on_demand: Read several global fields on demand (faster
            initialization) or on initialization (slower
            initialization, faster in individual seismogram extraction,
            useful e.g. for finite sources, default).
        :type read_on_demand: bool, optional
        """
        BaseNetCDFInstaseisDB.__init__(
            self, db_path=db_path, buffer_size_in_mb=buffer_size_in_mb,
            read_on_demand=read_on_demand, *args, **kwargs)
        self._parse_mesh(netcdf_file)

    def _parse_mesh(self, filename):

        MeshCollection_merged = collections.namedtuple(
            "MeshCollection_merged", ["merged"])

        self.meshes = MeshCollection_merged(mesh.Mesh(
            filename, full_parse=True,
            strain_buffer_size_in_mb=self.buffer_size_in_mb,
            displ_buffer_size_in_mb=self.buffer_size_in_mb,
            read_on_demand=self.read_on_demand))
        self.parsed_mesh = self.meshes.merged

        self._is_reciprocal = True

    def _get_data(self, source, receiver, components, coordinates,
                  element_info):
        ei = element_info
        # Collect data arrays and mu in a dictionary.
        data = {}

        mesh = self.parsed_mesh.f["Mesh"]

        # Get mu.
        if not self.read_on_demand:
            mesh_mu = self.parsed_mesh.mesh_mu
        else:
            mesh_mu = mesh["mesh_mu"]
        if self.info.dump_type == "displ_only":
            npol = self.info.spatial_order
            mu = mesh_mu[ei.gll_point_ids[npol // 2, npol // 2]]
        else:  # pragma: no cover
            # Merged databases currently not implemented for
            # non-displacement databases.
            raise NotImplementedError
            # XXX: Is this correct?
            mu = mesh_mu[ei.id_elem]
        data["mu"] = mu

        fac_1_map = {"N": np.cos,
                     "E": np.sin}
        fac_2_map = {"N": lambda x: - np.sin(x),
                     "E": np.cos}

        if isinstance(source, Source):
            if self.info.dump_type == 'displ_only':
                if ei.axis:
                    G = self.parsed_mesh.G2
                    GT = self.parsed_mesh.G1T
                else:
                    G = self.parsed_mesh.G2
                    GT = self.parsed_mesh.G2T

            if self.info.dump_type == 'displ_only':
                strain_x, strain_z = self._get_strain_interp(
                    ei.id_elem, ei.gll_point_ids, G, GT,
                    ei.col_points_xi, ei.col_points_eta, ei.corner_points,
                    ei.eltype, ei.axis, ei.xi, ei.eta)
            elif (self.info.dump_type == 'fullfields' or
                  self.info.dump_type == 'strain_only'):  # pragma: no cover
                # Merged databases currently not implemented for
                # non-displacement databases.
                raise NotImplementedError

            mij = rotations \
                .rotate_symm_tensor_voigt_xyz_src_to_xyz_earth(
                    source.tensor_voigt, np.deg2rad(source.longitude),
                    np.deg2rad(source.colatitude))
            mij = rotations \
                .rotate_symm_tensor_voigt_xyz_earth_to_xyz_src(
                    mij, np.deg2rad(receiver.longitude),
                    np.deg2rad(receiver.colatitude))
            mij = rotations.rotate_symm_tensor_voigt_xyz_to_src(
                mij, coordinates.phi)
            mij /= self.parsed_mesh.amplitude

            if "Z" in components:
                final = np.zeros(strain_z.shape[0], dtype="float64")
                for i in range(3):
                    final += mij[i] * strain_z[:, i]
                final += 2.0 * mij[4] * strain_z[:, 4]
                data["Z"] = final

            if "R" in components:
                final = np.zeros(strain_x.shape[0], dtype="float64")
                final -= strain_x[:, 0] * mij[0] * 1.0
                final -= strain_x[:, 1] * mij[1] * 1.0
                final -= strain_x[:, 2] * mij[2] * 1.0
                final -= strain_x[:, 4] * mij[4] * 2.0
                data["R"] = final

            if "T" in components:
                final = np.zeros(strain_x.shape[0], dtype="float64")
                final += strain_x[:, 3] * mij[3] * 2.0
                final += strain_x[:, 5] * mij[5] * 2.0
                data["T"] = final

            for comp in ["E", "N"]:
                if comp not in components:
                    continue

                fac_1 = fac_1_map[comp](coordinates.phi)
                fac_2 = fac_2_map[comp](coordinates.phi)

                final = np.zeros(strain_x.shape[0], dtype="float64")
                final += strain_x[:, 0] * mij[0] * 1.0 * fac_1
                final += strain_x[:, 1] * mij[1] * 1.0 * fac_1
                final += strain_x[:, 2] * mij[2] * 1.0 * fac_1
                final += strain_x[:, 3] * mij[3] * 2.0 * fac_2
                final += strain_x[:, 4] * mij[4] * 2.0 * fac_1
                final += strain_x[:, 5] * mij[5] * 2.0 * fac_2
                if comp == "N":
                    final *= -1.0
                data[comp] = final

        elif isinstance(source, ForceSource):
            if self.info.dump_type != 'displ_only':  # pragma: no cover
                # Merged databases currently not implemented for
                # non-displacement databases.
                raise NotImplementedError
                raise ValueError("Force sources only in displ_only mode")

            displ_x, displ_z = self._get_displacement(
                ei.id_elem, ei.gll_point_ids, ei.col_points_xi,
                ei.col_points_eta, ei.xi, ei.eta)

            force = rotations.rotate_vector_xyz_src_to_xyz_earth(
                source.force_tpr, np.deg2rad(source.longitude),
                np.deg2rad(source.colatitude))
            force = rotations.rotate_vector_xyz_earth_to_xyz_src(
                force, np.deg2rad(receiver.longitude),
                np.deg2rad(receiver.colatitude))
            force = rotations.rotate_vector_xyz_to_src(
                force, coordinates.phi)
            force /= self.parsed_mesh.amplitude

            if "Z" in components:
                final = np.zeros(displ_z.shape[0], dtype="float64")
                final += displ_z[:, 0] * force[0]
                final += displ_z[:, 2] * force[2]
                data["Z"] = final

            if "R" in components:
                final = np.zeros(displ_x.shape[0], dtype="float64")
                final += displ_x[:, 0] * force[0]
                final += displ_x[:, 2] * force[2]
                data["R"] = final

            if "T" in components:
                final = np.zeros(displ_x.shape[0], dtype="float64")
                final += displ_x[:, 1] * force[1]
                data["T"] = final

            for comp in ["E", "N"]:
                if comp not in components:
                    continue

                fac_1 = fac_1_map[comp](coordinates.phi)
                fac_2 = fac_2_map[comp](coordinates.phi)

                final = np.zeros(displ_x.shape[0], dtype="float64")
                final += displ_x[:, 0] * force[0] * fac_1
                final += displ_x[:, 1] * force[1] * fac_2
                final += displ_x[:, 2] * force[2] * fac_1
                if comp == "N":
                    final *= -1.0
                data[comp] = final

        else:
            raise NotImplementedError

        return data

    def _get_data_multiple(self, sources, receiver, components, coordinates,
                  element_info):
        ei = element_info
        # Collect data arrays and mu in a dictionary.
        data_all = []

        mesh = self.parsed_mesh.f["Mesh"]

        if self.info.dump_type != 'displ_only':
            raise ValueError("Force sources only in displ_only mode")

        # Get params
        if not self.read_on_demand:
            mesh_mu = self.parsed_mesh.mesh_mu
            mesh_rho = self.parsed_mesh.mesh_rho
            mesh_lambda = self.parsed_mesh.mesh_lambda
            mesh_xi = self.parsed_mesh.mesh_xi
            mesh_phi = self.parsed_mesh.mesh_phi
            mesh_eta = self.parsed_mesh.mesh_eta

        else:
            mesh_mu = mesh["mesh_mu"]
            mesh_rho = mesh["mesh_rho"]
            mesh_lambda = mesh["mesh_lambda"]
            mesh_xi = mesh["mesh_xi"]
            mesh_phi = mesh["mesh_phi"]
            mesh_eta = mesh["mesh_eta"]

        if self.info.dump_type == "displ_only":
            npol = self.info.spatial_order
            mu = mesh_mu[ei.gll_point_ids[npol // 2, npol // 2]]
            rho = mesh_rho[ei.gll_point_ids[npol // 2, npol // 2]]
            lbda = mesh_lambda[ei.gll_point_ids[npol // 2, npol // 2]]
            xi = mesh_xi[ei.gll_point_ids[npol // 2, npol // 2]]
            phi = mesh_phi[ei.gll_point_ids[npol // 2, npol // 2]]
            eta = mesh_eta[ei.gll_point_ids[npol // 2, npol // 2]]
        else:  # shouldn't happen
            raise ValueError

        params = {'mu': mu, 'rho': rho, 'lambda': lbda, 'xi': xi, 'phi': phi,
                  'eta': eta}

        fac_1_map = {"N": np.cos,
                     "E": np.sin}
        fac_2_map = {"N": lambda x: - np.sin(x),
                     "E": np.cos}

        if self.info.dump_type == 'displ_only':
            if ei.axis:
                G = self.parsed_mesh.G2
                GT = self.parsed_mesh.G1T
            else:
                G = self.parsed_mesh.G2
                GT = self.parsed_mesh.G2T
        else:  # shouldn't happen
            raise ValueError
        """
        displ_x, displ_z, strain_x, strain_z = \
            self._get_displacement_and_strain_interp(
            ei.id_elem, ei.gll_point_ids, G, GT,
            ei.col_points_xi, ei.col_points_eta, ei.corner_points,
            ei.eltype, ei.axis, ei.xi, ei.eta)
        """
        strain_x, strain_z = self._get_strain_interp(
            ei.id_elem, ei.gll_point_ids, G, GT,
            ei.col_points_xi, ei.col_points_eta, ei.corner_points,
            ei.eltype, ei.axis, ei.xi, ei.eta)

        displ_x, displ_z = self._get_displacement(
            ei.id_elem, ei.gll_point_ids, ei.col_points_xi,
            ei.col_points_eta, ei.xi, ei.eta)

        for _i, source in enumerate(sources.pointsources):
            data = {}
            if isinstance(source, Source):
                mij = rotations \
                    .rotate_symm_tensor_voigt_xyz_src_to_xyz_earth(
                        source.tensor_voigt, np.deg2rad(source.longitude),
                        np.deg2rad(source.colatitude))
                mij = rotations \
                    .rotate_symm_tensor_voigt_xyz_earth_to_xyz_src(
                        mij, np.deg2rad(receiver.longitude),
                        np.deg2rad(receiver.colatitude))
                mij = rotations.rotate_symm_tensor_voigt_xyz_to_src(
                    mij, coordinates.phi)
                mij /= self.parsed_mesh.amplitude

                if "Z" in components:
                    final = np.zeros(strain_z.shape[0], dtype="float64")
                    for i in range(3):
                        final += mij[i] * strain_z[:, i]
                    final += 2.0 * mij[4] * strain_z[:, 4]
                    data["Z"] = final

                if "R" in components:
                    final = np.zeros(strain_x.shape[0], dtype="float64")
                    final -= strain_x[:, 0] * mij[0] * 1.0
                    final -= strain_x[:, 1] * mij[1] * 1.0
                    final -= strain_x[:, 2] * mij[2] * 1.0
                    final -= strain_x[:, 4] * mij[4] * 2.0
                    data["R"] = final

                if "T" in components:
                    final = np.zeros(strain_x.shape[0], dtype="float64")
                    final += strain_x[:, 3] * mij[3] * 2.0
                    final += strain_x[:, 5] * mij[5] * 2.0
                    data["T"] = final

                for comp in ["E", "N"]:
                    if comp not in components:
                        continue

                    fac_1 = fac_1_map[comp](coordinates.phi)
                    fac_2 = fac_2_map[comp](coordinates.phi)

                    final = np.zeros(strain_x.shape[0], dtype="float64")
                    final += strain_x[:, 0] * mij[0] * 1.0 * fac_1
                    final += strain_x[:, 1] * mij[1] * 1.0 * fac_1
                    final += strain_x[:, 2] * mij[2] * 1.0 * fac_1
                    final += strain_x[:, 3] * mij[3] * 2.0 * fac_2
                    final += strain_x[:, 4] * mij[4] * 2.0 * fac_1
                    final += strain_x[:, 5] * mij[5] * 2.0 * fac_2
                    if comp == "N":
                        final *= -1.0
                    data[comp] = final
                data_all.append(data)

            elif isinstance(source, ForceSource):

                force = rotations.rotate_vector_xyz_src_to_xyz_earth(
                    source.force_tpr, np.deg2rad(source.longitude),
                    np.deg2rad(source.colatitude))
                force = rotations.rotate_vector_xyz_earth_to_xyz_src(
                    force, np.deg2rad(receiver.longitude),
                    np.deg2rad(receiver.colatitude))
                force = rotations.rotate_vector_xyz_to_src(
                    force, coordinates.phi)
                force /= self.parsed_mesh.amplitude

                if "Z" in components:
                    final = np.zeros(displ_z.shape[0], dtype="float64")
                    final += displ_z[:, 0] * force[0]
                    final += displ_z[:, 2] * force[2]
                    data["Z"] = final

                if "R" in components:
                    final = np.zeros(displ_x.shape[0], dtype="float64")
                    final += displ_x[:, 0] * force[0]
                    final += displ_x[:, 2] * force[2]
                    data["R"] = final

                if "T" in components:
                    final = np.zeros(displ_x.shape[0], dtype="float64")
                    final += displ_x[:, 1] * force[1]
                    data["T"] = final

                for comp in ["E", "N"]:
                    if comp not in components:
                        continue

                    fac_1 = fac_1_map[comp](coordinates.phi)
                    fac_2 = fac_2_map[comp](coordinates.phi)

                    final = np.zeros(displ_x.shape[0], dtype="float64")
                    final += displ_x[:, 0] * force[0] * fac_1
                    final += displ_x[:, 1] * force[1] * fac_2
                    final += displ_x[:, 2] * force[2] * fac_1
                    if comp == "N":
                        final *= -1.0
                    data[comp] = final
                data_all.append(data)

            else:
                raise NotImplementedError

        return params, data_all

    def _get_and_reorder_utemp(self, id_elem):
        # We can now read it in a single go!
        utemp = self.meshes.merged.f["MergedSnapshots"][id_elem]

        # utemp is currently (nvars, jpol, ipol, npts)
        # 1. Roll to (npts, nvar, jpol, ipol)
        utemp = np.rollaxis(utemp, 3, 0)
        # 2. Roll to (npts, jpol, nvar, ipol)
        utemp = np.rollaxis(utemp, 2, 1)
        # 3. Roll to (npts, jpol, ipol, nvar)
        utemp = np.rollaxis(utemp, 3, 2)

        return utemp

    def _get_strain_interp(self, id_elem, gll_point_ids, G, GT,
                           col_points_xi, col_points_eta, corner_points,
                           eltype, axis, xi, eta):
        mesh = self.meshes.merged
        if id_elem not in mesh.strain_buffer:
            utemp = self._get_and_reorder_utemp(id_elem)

            strain_fct_map = {
                "monopole": sem_derivatives.strain_monopole_td,
                "dipole": sem_derivatives.strain_dipole_td,
                "quadpole": sem_derivatives.strain_quadpole_td}

            # We want the cache to work - thus we always have to
            # calculate both! Also I/O is the slow part here.

            # Horizontal component is available if we have 3 or 5 components.
            if utemp.shape[-1] >= 3:
                utemp_x = utemp[:, :, :, :3]
                utemp_x = np.require(utemp_x, requirements=["F"],
                                     dtype=np.float64)
            #    np.save("strain_utemp_x_id_%i.npy" % (id_elem), utemp_x)
                strain_x = strain_fct_map["dipole"](
                    utemp_x, G, GT, col_points_xi, col_points_eta,
                    mesh.npol, mesh.ndumps, corner_points, eltype, axis)
            else:
                strain_x = None

            # Vertical component is available if we have 2 or 5 components.
            if utemp.shape[-1] in (2, 5):
                # Vertical expects disp_s at index 0 and disp_z at index 2.
                # Expand if only vertical.
                _s = list(utemp.shape)
                if _s[-1] == 2:
                    _s[-1] = 3
                    utemp_new = np.zeros(_s, dtype=utemp.dtype)
                    utemp_new[:, :, :, 0] = utemp[:, :, :, 0]
                    utemp_new[:, :, :, 2] = utemp[:, :, :, 1]
                    utemp_z = utemp_new
                # Reform all others.
                else:
                    utemp_z = utemp[:, :, :, -3:]
                    utemp_z[:, :, :, 0] = utemp_z[:, :, :, 1]
                    utemp_z[:, :, :, 1][:] = 0
                    utemp_z = np.require(utemp_z, requirements=["F"],
                                         dtype=np.float64)
            #    np.save("strain_utemp_z_id_%i.npy" % (id_elem), utemp_z)

                strain_z = strain_fct_map["monopole"](
                    utemp_z, G, GT, col_points_xi, col_points_eta,
                    mesh.npol, mesh.ndumps, corner_points, eltype, axis)
            else:
                strain_z = None

            mesh.strain_buffer.add(id_elem, (strain_x, strain_z))
        else:
            strain_x, strain_z = mesh.strain_buffer.get(id_elem)

        all_strains = {}
        for name, strain in (("strain_x", strain_x), ("strain_z", strain_z)):
            if strain is None:
                all_strains[name] = None
                continue
            final_strain = np.empty((strain.shape[0], 6), order="F")

            for i in range(6):
                final_strain[:, i] = spectral_basis.lagrange_interpol_2D_td(
                    col_points_xi, col_points_eta, strain[:, :, :, i], xi, eta)

            if not name == "strain_z":
                final_strain[:, 3] *= -1.0
                final_strain[:, 5] *= -1.0

            all_strains[name] = final_strain

        return all_strains["strain_x"], all_strains["strain_z"]

    def _get_displacement(self, id_elem, gll_point_ids,
                          col_points_xi, col_points_eta, xi, eta):
        mesh = self.meshes.merged
        if id_elem not in mesh.displ_buffer:
            utemp = self._get_and_reorder_utemp(id_elem)

            utemp_x = utemp[:, :, :, :3]
            utemp_x = np.require(utemp_x, requirements=["F"],
                                 dtype=np.float64)
            #np.save("displ_utemp_x_id_%i.npy" % (id_elem), utemp_x)

            utemp_z = utemp[:, :, :, -3:]
            utemp_z[:, :, :, 0] = utemp_z[:, :, :, 1]
            utemp_z[:, :, :, 1][:] = 0
            utemp_z = np.require(utemp_z, requirements=["F"], dtype=np.float64)
            #np.save("displ_utemp_z_id_%i.npy" % (id_elem), utemp_z)
            mesh.displ_buffer.add(id_elem, (utemp_x, utemp_z))
        else:
            utemp_x, utemp_z \
                = mesh.displ_buffer.get(id_elem)

        final_displacement_x = np.empty((utemp_x.shape[0], 3), order="F")
        final_displacement_z = np.empty((utemp_z.shape[0], 3), order="F")

        for i in range(3):
            final_displacement_x[:, i] = \
                spectral_basis.lagrange_interpol_2D_td(
                    col_points_xi, col_points_eta,
                    utemp_x[:, :, :, i], xi, eta)
            final_displacement_z[:, i] = \
                spectral_basis.lagrange_interpol_2D_td(
                    col_points_xi, col_points_eta,
                    utemp_z[:, :, :, i], xi, eta)

        return final_displacement_x, final_displacement_z

    def _get_displacement_and_strain_interp(self, id_elem, gll_point_ids, G, GT,
                          col_points_xi, col_points_eta, corner_points,
                           eltype, axis, xi, eta):
        mesh = self.meshes.merged
        if id_elem not in mesh.strain_buffer and id_elem not in \
                mesh.displ_buffer:
            utemp = self._get_and_reorder_utemp(id_elem)

            strain_fct_map = {
                "monopole": sem_derivatives.strain_monopole_td,
                "dipole": sem_derivatives.strain_dipole_td,
                "quadpole": sem_derivatives.strain_quadpole_td}

            # We want the cache to work - thus we always have to
            # calculate both! Also I/O is the slow part here.

            # Horizontal component is available if we have 3 or 5 components.
            if utemp.shape[-1] >= 3:
                final_displacement_x = np.empty((utemp.shape[0], 3), order="F")
                utemp_x = utemp[:, :, :, :3]
                utemp_x = np.require(utemp_x, requirements=["F"],
                                     dtype=np.float64)
                #np.save("strain_utemp_x_id_%i.npy" % (id_elem), utemp_x)
                for i in range(3):
                    final_displacement_x[:, i] = \
                        spectral_basis.lagrange_interpol_2D_td(
                            col_points_xi, col_points_eta,
                            utemp_x[:, :, :, i], xi, eta)
                strain_x = strain_fct_map["dipole"](
                    utemp_x, G, GT, col_points_xi, col_points_eta,
                    mesh.npol, mesh.ndumps, corner_points, eltype, axis)
            else:
                strain_x = None
                final_displacement_x = None

            # Vertical component is available if we have 2 or 5 components.
            if utemp.shape[-1] in (2, 5):
                # Vertical expects disp_s at index 0 and disp_z at index 2.
                # Expand if only vertical.
                _s = list(utemp.shape)
                if _s[-1] == 2:
                    _s[-1] = 3
                    utemp_new = np.zeros(_s, dtype=utemp.dtype)
                    utemp_new[:, :, :, 0] = utemp[:, :, :, 0]
                    utemp_new[:, :, :, 2] = utemp[:, :, :, 1]
                    utemp_z = utemp_new
                # Reform all others.
                else:
                    utemp_z = utemp[:, :, :, -3:]
                    utemp_z[:, :, :, 0] = utemp_z[:, :, :, 1]
                    utemp_z[:, :, :, 1][:] = 0
                    utemp_z = np.require(utemp_z, requirements=["F"],
                                         dtype=np.float64)
                final_displacement_z = np.empty((utemp_z.shape[0], 3),
                                                order="F")
                for i in range(3):
                    final_displacement_z[:, i] = \
                        spectral_basis.lagrange_interpol_2D_td(
                            col_points_xi, col_points_eta,
                            utemp_z[:, :, :, i], xi, eta)
                #np.save("strain_utemp_z_id_%i.npy" % (id_elem), utemp_z)

                strain_z = strain_fct_map["monopole"](
                    utemp_z, G, GT, col_points_xi, col_points_eta,
                    mesh.npol, mesh.ndumps, corner_points, eltype, axis)
                np.save("strain_z_id_%i.npy" % (id_elem), utemp_z)

            else:
                strain_z = None
                final_displacement_z = None

            mesh.strain_buffer.add(id_elem, (strain_x, strain_z))
            mesh.displ_buffer.add(id_elem, (final_displacement_x,
                                                   final_displacement_z))

        else:
            strain_x, strain_z = mesh.strain_buffer.get(id_elem)
            final_displacement_x, final_displacement_z = \
                mesh.displ_buffer.get(id_elem)

        np.save("strain_z_id_%i.npy" % (id_elem), strain_x)
        np.save("strain_x_id_%i.npy" % (id_elem), strain_z)

        all_strains = {}
        for name, strain in (("strain_x", strain_x), ("strain_z", strain_z)):
            if strain is None:
                all_strains[name] = None
                continue
            final_strain = np.empty((strain.shape[0], 6), order="F")

            for i in range(6):
                final_strain[:, i] = spectral_basis.lagrange_interpol_2D_td(
                    col_points_xi, col_points_eta, strain[:, :, :, i], xi, eta)

            if not name == "strain_z":
                final_strain[:, 3] *= -1.0
                final_strain[:, 5] *= -1.0

            all_strains[name] = final_strain

        return final_displacement_x, final_displacement_z, \
               all_strains["strain_x"], all_strains["strain_z"]

    def _get_params(self, element_info):

        ei = element_info

        mesh = self.parsed_mesh.f["Mesh"]

        if not self.read_on_demand:
            mesh_mu = self.parsed_mesh.mesh_mu
            mesh_rho = self.parsed_mesh.mesh_rho
            mesh_lambda = self.parsed_mesh.mesh_lambda
            mesh_xi = self.parsed_mesh.mesh_xi
            mesh_phi = self.parsed_mesh.mesh_phi
            mesh_eta = self.parsed_mesh.mesh_eta

        else:
            mesh_mu = mesh["mesh_mu"]
            mesh_rho = mesh["mesh_rho"]
            mesh_lambda = mesh["mesh_lambda"]
            mesh_xi = mesh["mesh_xi"]
            mesh_phi = mesh["mesh_phi"]
            mesh_eta = mesh["mesh_eta"]

        npol = self.info.spatial_order
        mu = mesh_mu[ei.gll_point_ids[npol // 2, npol // 2]]
        rho = mesh_rho[ei.gll_point_ids[npol // 2, npol // 2]]
        lbda = mesh_lambda[ei.gll_point_ids[npol // 2, npol // 2]]
        xi = mesh_xi[ei.gll_point_ids[npol // 2, npol // 2]]
        phi = mesh_phi[ei.gll_point_ids[npol // 2, npol // 2]]
        eta = mesh_eta[ei.gll_point_ids[npol // 2, npol // 2]]

        params = {'mu': mu, 'rho': rho, 'lambda': lbda, 'xi': xi, 'phi': phi,
                  'eta': eta}

        return params
