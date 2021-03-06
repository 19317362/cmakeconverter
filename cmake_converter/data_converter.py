#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright (c) 2016-2019:
#   Matthieu Estrada, ttamalfor@gmail.com
#   Pavel Liavonau, liavonlida@gmail.com
#
# This file is part of (CMakeConverter).
#
# (CMakeConverter) is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# (CMakeConverter) is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with (CMakeConverter).  If not, see <http://www.gnu.org/licenses/>.

"""
    DataConverter
    =============
     Manage conversion of data into CMake
"""

import os
from collections import OrderedDict
from multiprocessing import Pool
import shutil
import copy

from cmake_converter.data_files import get_cmake_lists
from cmake_converter.flags import Flags
from cmake_converter.utils import write_comment, message, write_arch_types,\
    write_use_package_stub, set_unix_slash
from cmake_converter.context import Context


class DataConverter:
    """
        Base class for converters
    """

    @staticmethod
    def collect_data(context):
        """
        Collect data for converter.

        """

        message(context, 'Collecting data for project {}'.format(context.vcxproj_path), '')
        context.parser.parse(context)

        context.files.find_cmake_project_languages(context)

    def verify_data(self, context):
        """ Verify procedure after gathering information from source project """
        self.__verify_target_types(context)
        return self.__verify_configurations_to_parse(context)

    @staticmethod
    def __verify_target_types(context):
        target_types = set()
        for setting in context.settings:
            if None in setting:
                continue
            target_types.add(context.settings[setting]['target_type'])

        if len(target_types) > 1:
            message(
                context,
                'Target has more than one output binary type. CMake does not support it!',
                'warn'
            )

    @staticmethod
    def __verify_configurations_to_parse(context):
        absent_settings = set()
        for setting in context.configurations_to_parse:
            if setting not in context.settings:
                absent_settings.add(setting)

        if len(absent_settings) > 0:
            context.configurations_to_parse -= absent_settings
            message(
                context,
                'There are absent settings at {}: {}\n'
                'skipping conversion. Add lost settings or fix mapping of settings at solution'
                .format(context.vcxproj_path, absent_settings),
                'error'
            )
            return False
        return True

    def merge_data_settings(self, context):
        """
        Merge common settings found among configuration settings (reduce copy-paste)

        :param context:
        :return:
        """

        for key in context.utils.lists_of_settings_to_merge():
            lists_of_items_to_merge = {}
            set_of_items = {}

            # get intersection pass
            for sln_setting in context.sln_configurations_map:
                mapped_setting = context.sln_configurations_map[sln_setting]
                mapped_arch = sln_setting[1]
                if mapped_arch is not None and mapped_arch not in lists_of_items_to_merge:
                    lists_of_items_to_merge[mapped_arch] = OrderedDict()
                if (None, mapped_arch) not in context.settings:
                    context.current_setting = (None, mapped_arch)
                    context.utils.init_context_current_setting(context)

                if key not in context.settings[mapped_setting] \
                        or mapped_setting[0] is None:
                    continue
                settings_list = context.settings[mapped_setting][key]
                if not lists_of_items_to_merge[mapped_arch]:  # first pass
                    set_of_items[mapped_arch] = set(settings_list)

                lists_of_items_to_merge[mapped_arch][sln_setting] = settings_list
                set_of_items[mapped_arch] = set_of_items[mapped_arch].intersection(
                    set(context.settings[mapped_setting][key])
                )

            self.__remove_common_settings_from_context(
                context,
                lists_of_items_to_merge,
                set_of_items,
                key
            )

            merged_order_lists = self.__get_order_of_common_settings(lists_of_items_to_merge)

            merged_settings = self.__get_common_ordered_settings(
                merged_order_lists,
                set_of_items
            )

            for arch in merged_settings:
                merged_setting = merged_settings[arch]
                context.settings[(None, arch)][key] = merged_setting
                context.sln_configurations_map[(None, arch)] = (None, arch)

        if context.file_contexts is not None:
            for file in context.file_contexts:
                self.merge_data_settings(context.file_contexts[file])

    def __remove_common_settings_from_context(
            self, context, lists_of_items_to_merge, set_of_items, key
    ):
        """ Removing common settings from configurations """
        for arch in lists_of_items_to_merge:
            for sln_setting in lists_of_items_to_merge[arch]:
                result_settings_list = []
                for element in lists_of_items_to_merge[arch][sln_setting]:
                    if element not in set_of_items[arch]:
                        result_settings_list.append(element)
                self.__update_settings_at_context(
                    context, sln_setting, key, result_settings_list
                )

    @staticmethod
    def __update_settings_at_context(context, sln_setting, key, settings_to_set):
        if sln_setting not in context.settings:
            context.settings[sln_setting] = \
                copy.deepcopy(context.settings[context.sln_configurations_map[sln_setting]])
        context.sln_configurations_map[sln_setting] = sln_setting
        context.settings[sln_setting][key] = settings_to_set

    @staticmethod
    def __get_order_of_common_settings(lists_of_items_to_merge_arch):
        merged_order_lists = {}
        for arch in lists_of_items_to_merge_arch:
            lists_of_items_to_merge = lists_of_items_to_merge_arch[arch]
            merged_order_lists[arch] = []
            i = 0
            while True:
                out_of_bounds = 0
                for setting in lists_of_items_to_merge:
                    settings_list = lists_of_items_to_merge[setting]
                    if i < len(settings_list):
                        merged_order_lists[arch].append(settings_list[i])
                    else:
                        out_of_bounds += 1

                if out_of_bounds == len(lists_of_items_to_merge):
                    break
                i += 1

        return merged_order_lists

    @staticmethod
    def __get_common_ordered_settings(merged_order_list_arch, set_of_items):
        common_ordered_lists = {}
        for arch in merged_order_list_arch:
            merged_order_list = merged_order_list_arch[arch]
            common_ordered_lists[arch] = []
            for element in merged_order_list:
                if element in set_of_items[arch]:
                    common_ordered_lists[arch].append(element)
                    set_of_items[arch].remove(element)
                    if not set_of_items:
                        break

        return common_ordered_lists

    @staticmethod
    def write_data(context, cmake_file):
        """
        Write data defined in converter.

        :param context: converter context
        :type context: Context
        :param cmake_file: CMakeLists IO wrapper
        :type cmake_file: _io.TextIOWrapper
        """

        context.files.write_cmake_project(context, cmake_file)

        # Add additional code or not
        if context.additional_code is not None:
            context.files.add_additional_code(context, context.additional_code, cmake_file)

        context.files.write_source_groups(context, cmake_file)

        if not context.has_only_headers:
            write_comment(cmake_file, 'Target')
            context.flags.write_target_artifact(context, cmake_file)
            context.flags.write_use_pch_function(context, cmake_file)
            context.dependencies.write_target_property_sheets(context, cmake_file)
            context.variables.write_target_outputs(context, cmake_file)
            context.dependencies.write_include_directories(context, cmake_file)
            context.flags.write_defines(context, cmake_file)
            context.flags.write_flags(context, cmake_file)
            context.dependencies.write_target_build_events(context, cmake_file)
            if (context.target_references
                    or context.add_lib_deps
                    or context.sln_deps
                    or context.packages):
                write_comment(cmake_file, 'Dependencies')
            context.dependencies.write_target_references(context, cmake_file)
            context.dependencies.write_link_dependencies(context, cmake_file)
            context.dependencies.write_target_dependency_packages(context, cmake_file)
        else:
            Flags.write_target_headers_only_artifact(context, cmake_file)

    def convert_project(self, context, xml_project_path, cmake_lists_destination_path):
        """
        Method template for data collecting and writing

        """
        # Initialize Context of DataConverter
        if not context.init(xml_project_path, cmake_lists_destination_path):
            return False

        message(context, 'Conversion started: Project {}'.format(context.project_name), 'done')
        self.collect_data(context)
        if not self.verify_data(context):
            return False
        self.merge_data_settings(context)
        if context.dry:
            return True
        if os.path.exists(context.cmake + '/CMakeLists.txt'):
            cmake_file = get_cmake_lists(context, context.cmake, 'a')
            cmake_file.write('\n' * 26)
        else:
            cmake_file = get_cmake_lists(context, context.cmake)
        message(context, 'Writing data for project {}'.format(context.vcxproj_path), '')
        self.write_data(context, cmake_file)
        cmake_file.close()
        warnings = ''
        if context.warnings_count > 0:
            warnings = ' ({} warnings)'.format(context.warnings_count)
        message(
            context,
            'Conversion done   : Project {}{}'.format(context.project_name, warnings), 'done'
        )

        return True

    def run_conversion(self, subdirectory_projects_data):
        """ Routine that converts projects located at the same directory """
        results = []
        for project_data in subdirectory_projects_data:
            project_context = project_data['project_context']
            name = project_context.project_number
            message(project_context, '------ Starting {} -------'.format(name), '')
            converted = self.convert_project(
                project_context,
                project_data['project_abs'],
                project_data['subdirectory'],
            )
            message(project_context, '------ Exiting  {} -------'.format(name), '')

            if not converted:
                continue

            project_context = project_data['project_context']
            # Can't return context as a result due PicklingError
            results.append(
                {
                    'cmake': project_context.cmake,
                    'project_name': project_context.project_name,
                    'solution_languages': project_context.solution_languages,
                    'target_windows_ver': project_context.target_windows_version,
                    'warnings_count': project_context.warnings_count
                }
            )
        return results

    def do_conversion(self, root_context, input_data_for_converter):
        """ Executes conversion with given projects input data """
        input_converter_data_list = []
        for subdirectory in input_data_for_converter:
            input_converter_data_list.append(input_data_for_converter[subdirectory])

        results = []
        if root_context.jobs > 1:
            pool = Pool(root_context.jobs)
            results = pool.map(self.run_conversion, input_converter_data_list)
        else:   # do in main thread
            for data_for_converter in input_converter_data_list:
                results.append(self.run_conversion(data_for_converter))

        return results

    def write_root_cmake_file(
            self,
            root_context,
            configuration_types_list,
            subdirectories_set,
            subdirectories_to_project_name
    ):
        """ Routine that writes entry point of converted solution for CMake """

        if root_context.dry:
            return

        root_cmake = get_cmake_lists(root_context, root_context.solution_path, 'r')
        root_cmake_projects_text = ''
        if root_cmake is not None:
            root_cmake_projects_text = root_cmake.read()
            root_cmake.close()

        root_cmake = get_cmake_lists(root_context, root_context.solution_path)
        root_cmake.write('cmake_minimum_required(VERSION 3.13.0 FATAL_ERROR)\n\n')
        if root_context.target_windows_version:
            root_cmake.write(
                'set(CMAKE_SYSTEM_VERSION {} CACHE STRING "" FORCE)\n\n'
                .format(root_context.target_windows_version)
            )
        language_command = 'project'
        project_name_var = root_context.project_name + ' '
        if root_cmake_projects_text:
            language_command = 'enable_language'
            project_name_var = ''
        root_cmake.write(
            '{}({}{})\n\n'.format(
                language_command,
                project_name_var,
                ' '.join(sorted(root_context.solution_languages))
            )
        )

        write_arch_types(root_context, root_cmake)

        self.__write_supported_architectures_check(root_context, root_cmake)
        self.__write_global_configuration_types(root_context, root_cmake, configuration_types_list)

        self.__write_global_compile_options(
            root_context, root_cmake, configuration_types_list
        )

        self.__write_global_link_options(root_context, root_cmake, configuration_types_list)

        write_use_package_stub(root_context, root_cmake)

        write_comment(root_cmake, 'Common utils')
        root_cmake.write('include(CMake/Utils.cmake)\n\n')
        self.copy_cmake_utils(root_context.solution_path)

        write_comment(root_cmake, 'Additional Global Settings(add specific info there)')
        root_cmake.write('include(CMake/GlobalSettingsInclude.cmake OPTIONAL)\n\n')

        write_comment(root_cmake, 'Use solution folders feature')
        root_cmake.write('set_property(GLOBAL PROPERTY USE_FOLDERS ON)\n\n')

        self.__write_subdirectories(
            root_cmake, subdirectories_set, subdirectories_to_project_name
        )

        if root_cmake_projects_text:
            root_cmake.write('\n' * 26)
            root_cmake.write(root_cmake_projects_text)
            root_cmake.write('project({})\n'.format(root_context.project_name))

        root_cmake.close()

        warnings = ''
        if root_context.warnings_count > 0:
            warnings = ' ({} warnings)'.format(root_context.warnings_count)
        message(
            root_context,
            'Conversion of {} finished{}\n\nNow you may run cmake like following sample:\n\n'
            'cmake -S{} -B{} -G"Visual Studio 15 2017 Win64"'.format(
                root_context.vcxproj_path,
                warnings,
                root_context.cmake,
                os.path.join(root_context.cmake, 'build')
            ),
            'done'
        )

    @staticmethod
    def __write_supported_architectures_check(context, cmake_file):
        arch_list = list(context.supported_architectures)
        arch_list.sort()
        cmake_file.write('if(NOT (')
        first = True
        for arch in arch_list:
            if first:
                cmake_file.write('\"${{CMAKE_VS_PLATFORM_NAME}}\" STREQUAL \"{}\"'
                                 .format(arch))
                first = False
            else:
                cmake_file.write('\n{} OR \"${{CMAKE_VS_PLATFORM_NAME}}\" STREQUAL \"{}\"'
                                 .format(context.indent, arch))
        cmake_file.write('))\n')
        cmake_file.write(
            '{}message(FATAL_ERROR "${{CMAKE_VS_PLATFORM_NAME}} arch is not supported!")\n'
            .format(context.indent)
        )
        cmake_file.write('endif()\n\n')

    @staticmethod
    def copy_cmake_utils(cmake_lists_path):
        """ Copy necessary util files into CMake folder """
        utils_path = os.path.join(cmake_lists_path, 'CMake')
        if not os.path.exists(utils_path):
            os.makedirs(utils_path)
        src_dir = os.path.dirname(os.path.abspath(__file__))
        shutil.copyfile(os.path.join(src_dir, 'utils.cmake'), utils_path + '/Utils.cmake')

    @staticmethod
    def __write_global_configuration_types(context, root_cmake, configuration_types_list):
        write_comment(root_cmake, 'Global configuration types')
        root_cmake.write('set(CMAKE_CONFIGURATION_TYPES\n')
        for configuration_type in configuration_types_list:
            root_cmake.write('{}\"{}\"\n'.format(context.indent, configuration_type))
        root_cmake.write('{}CACHE STRING "" FORCE\n)\n\n'.format(context.indent))

    def __write_global_compile_options(self, root_context, root_cmake, configuration_types_list):
        write_comment(root_cmake, 'Global compiler options')
        root_cmake.write('if(MSVC)\n')
        root_cmake.write(
            '{}# remove default flags provided with CMake for MSVC\n'.format(root_context.indent)
        )
        have_fortran = False
        for lang in sorted(root_context.solution_languages):
            if lang == 'Fortran':
                have_fortran = True
                continue
            self.__write_global_compile_options_language(
                root_context, root_cmake, configuration_types_list, lang
            )
        root_cmake.write('endif()\n\n')

        if have_fortran:
            root_cmake.write('if(${CMAKE_Fortran_COMPILER_ID} STREQUAL "Intel")\n')
            root_cmake.write(
                '{}# remove default flags provided with CMake for ifort\n'
                .format(root_context.indent)
            )
            self.__write_global_compile_options_language(
                root_context, root_cmake, configuration_types_list, 'Fortran'
            )
            root_cmake.write('endif()\n\n')

    @staticmethod
    def __write_global_compile_options_language(
            context, root_cmake, configuration_types_list, lang
    ):
        root_cmake.write('{}set(CMAKE_{}_FLAGS "")\n'.format(context.indent, lang))
        for configuration_type in configuration_types_list:
            root_cmake.write('{}set(CMAKE_{}_FLAGS_{} "")\n'
                             .format(context.indent, lang, configuration_type.upper()))

    @staticmethod
    def __write_global_link_options(context, root_cmake, configuration_types_list):
        write_comment(root_cmake, 'Global linker options')
        root_cmake.write('if(MSVC)\n')
        root_cmake.write(
            '{}# remove default flags provided with CMake for MSVC\n'.format(context.indent)
        )
        root_cmake.write('{}set(CMAKE_EXE_LINKER_FLAGS "")\n'.format(context.indent))
        root_cmake.write('{}set(CMAKE_MODULE_LINKER_FLAGS "")\n'.format(context.indent))
        root_cmake.write('{}set(CMAKE_SHARED_LINKER_FLAGS "")\n'.format(context.indent))
        root_cmake.write('{}set(CMAKE_STATIC_LINKER_FLAGS "")\n'.format(context.indent))
        for configuration_type in configuration_types_list:
            ct_upper = configuration_type.upper()
            root_cmake.write(
                '{}set(CMAKE_EXE_LINKER_FLAGS_{} \"${{CMAKE_EXE_LINKER_FLAGS}}\")\n'
                .format(context.indent, ct_upper))
            root_cmake.write(
                '{}set(CMAKE_MODULE_LINKER_FLAGS_{} \"${{CMAKE_MODULE_LINKER_FLAGS}}\")\n'
                .format(context.indent, ct_upper))
            root_cmake.write(
                '{}set(CMAKE_SHARED_LINKER_FLAGS_{} \"${{CMAKE_SHARED_LINKER_FLAGS}}\")\n'
                .format(context.indent, ct_upper))
            root_cmake.write(
                '{}set(CMAKE_STATIC_LINKER_FLAGS_{} \"${{CMAKE_STATIC_LINKER_FLAGS}}\")\n'
                .format(context.indent, ct_upper))
        root_cmake.write('endif()\n\n')

    @staticmethod
    def __write_subdirectories(root_cmake, subdirectories_set, subdirectories_to_project_name):
        write_comment(root_cmake, 'Sub-projects')
        subdirectories = list(subdirectories_set)
        subdirectories.sort(key=str.lower)
        for subdirectory in subdirectories:
            binary_dir = ''
            if '.' in subdirectory[:1]:
                binary_dir = ' ${{CMAKE_BINARY_DIR}}/{}'.format(
                    subdirectories_to_project_name[subdirectory])
            root_cmake.write('add_subdirectory({}{})\n'.format(
                set_unix_slash(subdirectory), binary_dir))
        root_cmake.write('\n')
