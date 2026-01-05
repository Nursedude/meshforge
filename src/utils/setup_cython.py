#!/usr/bin/env python3
"""
Build script for Cython-optimized MeshForge modules.

Usage:
    cd src/utils
    python setup_cython.py build_ext --inplace

Or from project root:
    python src/utils/setup_cython.py build_ext --inplace

Requirements:
    pip install cython

The compiled modules provide 5-10x speedup for RF calculations
and network simulation. Falls back to pure Python if not compiled.
"""

import os
import sys
from pathlib import Path

try:
    from setuptools import setup, Extension
    from Cython.Build import cythonize
    HAS_CYTHON = True
except ImportError:
    HAS_CYTHON = False
    print("Cython not installed. Install with: pip install cython")
    print("Falling back to pure Python implementations.")
    sys.exit(0)


# Get the directory containing this script
SCRIPT_DIR = Path(__file__).parent.absolute()

# Define extensions
extensions = [
    Extension(
        "rf_fast",
        sources=[str(SCRIPT_DIR / "rf_fast.pyx")],
        extra_compile_args=["-O3", "-ffast-math"],  # Aggressive optimization
    ),
]

# Compiler directives for Cython
compiler_directives = {
    'language_level': 3,
    'boundscheck': False,
    'wraparound': False,
    'cdivision': True,
    'nonecheck': False,
}

if __name__ == "__main__":
    # Change to script directory for build
    os.chdir(SCRIPT_DIR)

    setup(
        name="meshforge_rf_fast",
        version="1.0.0",
        description="Cython-optimized RF calculations for MeshForge",
        ext_modules=cythonize(
            extensions,
            compiler_directives=compiler_directives,
            annotate=True,  # Generate HTML annotation file
        ),
        zip_safe=False,
    )

    print("\n✓ Cython modules compiled successfully!")
    print("  rf_fast module is now available for import.")
