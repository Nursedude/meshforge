#!/usr/bin/env python3
"""Setup script for Meshtasticd Interactive Installer"""

from setuptools import setup, find_packages
from pathlib import Path

# Read README
readme_file = Path(__file__).parent / 'README.md'
long_description = readme_file.read_text() if readme_file.exists() else ''

# Read requirements
requirements_file = Path(__file__).parent / 'requirements.txt'
requirements = []
if requirements_file.exists():
    requirements = [
        line.strip()
        for line in requirements_file.read_text().splitlines()
        if line.strip() and not line.startswith('#')
    ]

setup(
    name='meshtasticd-installer',
    version='1.0.0',
    description='Interactive installer and manager for meshtasticd on Raspberry Pi OS',
    long_description=long_description,
    long_description_content_type='text/markdown',
    author='Your Name',
    url='https://github.com/Nursedude/Meshtasticd_interactive_UI',
    license='GPL-3.0',
    packages=find_packages(where='src'),
    package_dir={'': 'src'},
    install_requires=requirements,
    python_requires='>=3.7',
    entry_points={
        'console_scripts': [
            'meshtasticd-installer=main:main',
        ],
    },
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'Intended Audience :: System Administrators',
        'License :: OSI Approved :: GNU General Public License v3 (GPLv3)',
        'Operating System :: POSIX :: Linux',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Topic :: System :: Installation/Setup',
        'Topic :: System :: Systems Administration',
    ],
    keywords='meshtastic meshtasticd installer raspberry-pi lora mesh-network',
    project_urls={
        'Bug Reports': 'https://github.com/Nursedude/Meshtasticd_interactive_UI/issues',
        'Source': 'https://github.com/Nursedude/Meshtasticd_interactive_UI',
        'Meshtastic': 'https://meshtastic.org/',
    },
    include_package_data=True,
    zip_safe=False,
)
