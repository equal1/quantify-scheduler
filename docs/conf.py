#!/usr/bin/env python
# pylint: disable=wrong-import-position,unused-import
#
# quantify documentation build configuration file, created by
# sphinx-quickstart on Fri Jun  9 13:47:02 2017.
#
# This file is execfile()d with the current directory set to its
# containing dir.
#
# Note that not all possible configuration values are present in this
# autogenerated file.
#
# All configuration values have a default; values that are commented out
# serve to show the default.

# If extensions (or modules to document with autodoc) are in another
# directory, add these directories to sys.path here. If the directory is
# relative to the documentation root, use os.path.abspath to make it
# absolute, like shown here.
#
import os
import sys

package_path = os.path.abspath("..")
sys.path.insert(0, package_path)

# -- General configuration ---------------------------------------------
# pylint: disable=invalid-name

# If your documentation needs a minimal Sphinx version, state it here.
#
# needs_sphinx = '1.0'

# Add any Sphinx extension module names here, as strings. They can be
# extensions coming with Sphinx (named 'sphinx.ext.*') or your custom ones.
extensions = [
    "sphinx.ext.autodoc",  # auto document docstrings
    "sphinx.ext.napoleon",  # autodoc understands numpy docstrings
    # load after napoleon, improved compatibility with type hints annotations
    "sphinx_autodoc_typehints",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinx.ext.autosectionlabel",
    "sphinx-jsonschema",
    "sphinx_rtd_theme",
    "sphinx.ext.mathjax",
    "sphinx.ext.todo",
    "sphinx-jsonschema",
    "jupyter_sphinx",
    "sphinxcontrib.blockdiag",
    "sphinx_togglebutton",
    # fancy type hints in docs and
    # solves the same issue as "sphinx_automodapi.smart_resolver"
    "scanpydoc.elegant_typehints",
    # "enum_tools.autoenum",
]

# Add any paths that contain templates here, relative to this directory.
templates_path = ["_templates"]

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "qcodes": ("https://qcodes.github.io/Qcodes/", None),
    "xarray": ("https://xarray.pydata.org/en/stable/", None),
    "numpy": ("https://numpy.org/doc/stable/", None),
    "matplotlib": ("https://matplotlib.org/stable/", None),
    "lmfit": ("https://lmfit.github.io/lmfit-py/", None),
    "dateutil": ("https://dateutil.readthedocs.io/en/stable/", None),
    "jsonschema": ("https://python-jsonschema.readthedocs.io/en/stable/", None),
    "quantify-core": (
        "https://quantify-quantify-core.readthedocs-hosted.com/en/develop/",
        None,
    ),
    "scipy": ("https://docs.scipy.org/doc/scipy/reference/", None),
    "qblox-instruments": (
        "https://qblox-qblox-instruments.readthedocs-hosted.com/en/master/",
        None,
    ),
    "zhinst-toolkit": ("https://docs.zhinst.com/zhinst-toolkit/en/latest/", None),
    "zhinst-qcodes": ("https://docs.zhinst.com/zhinst-qcodes/en/latest/", None),
}


# The suffix(es) of source filenames.
# You can specify multiple suffix as a list of string:
#
# source_suffix = ['.rst', '.md']
source_suffix = ".rst"

# The master toctree document.
master_doc = "index"

# General information about the project.
project = "Quantify-Scheduler"
copyright = "2020-2021, Qblox & Orange Quantum Systems"
author = "The Quantify consortium"


# The language for content autogenerated by Sphinx. Refer to documentation
# for a list of supported languages.
#
# This is also used if you do content translation via gettext catalogs.
# Usually you set "language" from the command line for these cases.
language = None

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# This patterns also effect to html_static_path and html_extra_path
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

# The name of the Pygments (syntax highlighting) style to use.
pygments_style = "sphinx"

# If true, `todo` and `todoList` produce output, else they produce nothing.
todo_include_todos = True

# Document __init__ docstring together with class doctring (when __init__ is present)
napoleon_include_init_with_doc = True
# NB the line below could be used for a similar result
# BUT the line below ALWAYS includes the __init__ docstring even if it come from the
# parent class which is undesired for analysis subclasses, for example.
# autoclass_content = "both"


# -- Options for HTML output -------------------------------------------

# The theme to use for HTML and HTML Help pages.  See the documentation for
# a list of builtin themes.
#
html_theme = "sphinx_rtd_theme"

# Theme options are theme-specific and customize the look and feel of a
# theme further.  For a list of options available for each theme, see the
# documentation.
#
# html_theme_options = {}

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
html_static_path = ["_static"]
html_css_files = [
    "quantify.css",
]


# -- Options for HTMLHelp output ---------------------------------------

# Output file base name for HTML help builder.
htmlhelp_basename = "quantifydoc"


# -- Options for LaTeX output ------------------------------------------

latex_elements = {
    # The paper size ('letterpaper' or 'a4paper').
    #
    # 'papersize': 'letterpaper',
    # The font size ('10pt', '11pt' or '12pt').
    #
    # 'pointsize': '10pt',
    # Additional stuff for the LaTeX preamble.
    #
    # 'preamble': '',
    # Latex figure (float) alignment
    #
    # 'figure_align': 'htbp',
}

# Grouping the document tree into LaTeX files. List of tuples
# (source start file, target name, title, author, documentclass
# [howto, manual, or own class]).
latex_documents = [
    (
        master_doc,
        "quantify.tex",
        "quantify Documentation",
        "Quantify Consortium ",
        "manual",
    ),
]


# -- Options for manual page output ------------------------------------

# One entry per manual page. List of tuples
# (source start file, name, description, authors, manual section).
man_pages = [(master_doc, "quantify", "quantify Documentation", [author], 1)]


# -- Options for Texinfo output ----------------------------------------

# Grouping the document tree into Texinfo files. List of tuples
# (source start file, target name, title, author,
#  dir menu entry, description, category)
texinfo_documents = [
    (
        master_doc,
        "Quantify-Scheduler",
        "Quantify-Scheduler Documentation",
        author,
        "Quantify-Scheduler",
        "One line description of project.",
        "Miscellaneous",
    ),
]

# -- Other Options -----------------------------------------------------

# avoid duplicate label warning even when manual label has been used
suppress_warnings = ["autosectionlabel.*"]

blockdiag_html_image_format = "SVG"

# used by scanpydoc.elegant_typehints to correctly link to external docs
qualname_overrides = {
    "matplotlib.axes._axes.Axes": "matplotlib.axes.Axes",
    "zhinst.qcodes.uhfqa.UHFQA": "zhinst.qcodes.UHFQA",
    "zhinst.qcodes.hdawg.HDAWG": "zhinst.qcodes.HDAWG",
}

numfig = True

autodoc_member_order = "groupwise"

# -- Options for auto documenting typehints ----------------------------

# Please see https://gitlab.com/quantify-os/quantify/-/issues/10 regarding

# below should be imported all "problematic" modules that might raise strange issues
# when building the docs
# e.g., to "partially initialized module", or "most likely due to a circular import"

# This is a practical solution. We tried fixing certain things upstream, e.g.:
# https://github.com/QCoDeS/Qcodes/pull/2909
# but the issues popped up again, so this is the best and easier solution so far

# qcodes imports scipy under the hood but since scipy=1.7.0 it needs to be imported
# here with typing.TYPE_CHECKING = True otherwise we run into quantify-core#
import typing

typing.TYPE_CHECKING = True
import scipy

# lmfit seem to be importing something from scipy that otherwise does not get imported
import lmfit

typing.TYPE_CHECKING = False

import qcodes
import marshmallow

# When building the docs we need `typing.TYPE_CHECKING` to be `True` so that the
# sphinx' kernel loads the modules corresponding to the typehints and is able to
# auto document types. The modules listed above create issues when loaded with
# `typing.TYPE_CHECKING = True` so we import them beforehand to avoid nasty issues.

# It is a good practice to make use of the following construct to import modules that
# are used for type hints ONLY! the construct is the following:

# if typing.TYPE_CHECKING:
#     import my_expensive_to_import_module as my_module

# NB if you run into any circular import issue it is because you are importing module
# member directly from a module, i.e.:

# if typing.TYPE_CHECKING:
#     from my_expensive_to_import_module import BlaClass # Potential circular import

set_type_checking_flag = True  # this will run `typing.TYPE_CHECKING = True`
