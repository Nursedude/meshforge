#!/usr/bin/env python3
"""
Standalone Verification Script
Tests that the meshtasticd installer can run independently
"""

import sys
import os
import importlib.util
import subprocess
from pathlib import Path

# Colors for terminal output
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    BOLD = '\033[1m'
    END = '\033[0m'

def print_header(text):
    print(f"\n{Colors.CYAN}{Colors.BOLD}{'='*60}{Colors.END}")
    print(f"{Colors.CYAN}{Colors.BOLD}{text.center(60)}{Colors.END}")
    print(f"{Colors.CYAN}{Colors.BOLD}{'='*60}{Colors.END}\n")

def print_success(text):
    print(f"{Colors.GREEN}✓ {text}{Colors.END}")

def print_error(text):
    print(f"{Colors.RED}✗ {text}{Colors.END}")

def print_warning(text):
    print(f"{Colors.YELLOW}⚠ {text}{Colors.END}")

def print_info(text):
    print(f"{Colors.BLUE}ℹ {text}{Colors.END}")

def check_python_version():
    """Check Python version"""
    print_header("Python Version Check")

    version = sys.version_info
    version_str = f"{version.major}.{version.minor}.{version.micro}"

    print_info(f"Python version: {version_str}")

    if version.major >= 3 and version.minor >= 7:
        print_success(f"Python {version_str} is supported")
        return True
    else:
        print_error(f"Python {version_str} is not supported (requires 3.7+)")
        return False

def check_dependencies():
    """Check all required Python dependencies"""
    print_header("Dependency Check")

    dependencies = {
        'click': 'click>=8.0.0',
        'rich': 'rich>=13.0.0',
        'yaml': 'pyyaml>=6.0',
        'requests': 'requests>=2.31.0',
        'psutil': 'psutil>=5.9.0',
        'distro': 'distro>=1.8.0',
        'dotenv': 'python-dotenv>=1.0.0'
    }

    all_found = True
    missing = []

    for module, package in dependencies.items():
        spec = importlib.util.find_spec(module)
        if spec is None:
            print_error(f"Missing: {package}")
            missing.append(package)
            all_found = False
        else:
            print_success(f"Found: {package}")

    if missing:
        print_warning("\nTo install missing dependencies:")
        print_info("sudo python3 -m pip install " + " ".join([p.split('>=')[0] for p in missing]))

    return all_found

def check_file_structure():
    """Check that all required files exist"""
    print_header("File Structure Check")

    required_files = [
        'src/main.py',
        'src/utils/system.py',
        'src/utils/logger.py',
        'src/installer/meshtasticd.py',
        'src/installer/dependencies.py',
        'src/config/device.py',
        'src/config/lora.py',
        'src/config/hardware.py',
        'requirements.txt',
        'README.md',
        'install.sh',
        'web_installer.py',
        'Dockerfile',
        'docker-compose.yml'
    ]

    all_found = True
    base_path = Path(__file__).parent

    for file_path in required_files:
        full_path = base_path / file_path
        if full_path.exists():
            print_success(f"Found: {file_path}")
        else:
            print_error(f"Missing: {file_path}")
            all_found = False

    return all_found

def check_scripts_executable():
    """Check that shell scripts are executable"""
    print_header("Script Permissions Check")

    scripts = [
        'install.sh',
        'web_installer.py',
        'docker-entrypoint.sh'
    ]

    all_executable = True
    base_path = Path(__file__).parent

    for script in scripts:
        script_path = base_path / script
        if script_path.exists():
            if os.access(script_path, os.X_OK):
                print_success(f"Executable: {script}")
            else:
                print_warning(f"Not executable: {script}")
                print_info(f"  Run: chmod +x {script}")
                all_executable = False
        else:
            print_error(f"Not found: {script}")
            all_executable = False

    return all_executable

def test_import_main():
    """Test importing main module"""
    print_header("Module Import Test")

    try:
        # Add src to path
        base_path = Path(__file__).parent
        src_path = str(base_path / 'src')
        if src_path not in sys.path:
            sys.path.insert(0, src_path)

        # Try importing main modules
        modules_to_test = [
            'utils.system',
            'utils.logger',
            'installer.meshtasticd',
            'config.device'
        ]

        all_imported = True
        for module_name in modules_to_test:
            try:
                importlib.import_module(module_name)
                print_success(f"Successfully imported: {module_name}")
            except ImportError as e:
                print_error(f"Failed to import {module_name}: {str(e)}")
                all_imported = False

        return all_imported

    except Exception as e:
        print_error(f"Import test failed: {str(e)}")
        return False

def test_cli_help():
    """Test running the CLI with --help"""
    print_header("CLI Help Test")

    try:
        base_path = Path(__file__).parent
        main_script = base_path / 'src' / 'main.py'

        result = subprocess.run(
            ['python3', str(main_script), '--help'],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode == 0 and 'Meshtasticd' in result.stdout:
            print_success("CLI help command works")
            return True
        else:
            print_error(f"CLI help failed with return code {result.returncode}")
            if result.stderr:
                print_info(f"Error: {result.stderr}")
            return False

    except subprocess.TimeoutExpired:
        print_error("CLI help command timed out")
        return False
    except Exception as e:
        print_error(f"CLI test failed: {str(e)}")
        return False

def test_web_installer():
    """Test that web installer can be imported"""
    print_header("Web Installer Test")

    try:
        # Check if web_installer.py exists and has required components
        base_path = Path(__file__).parent
        web_installer = base_path / 'web_installer.py'

        if web_installer.exists():
            with open(web_installer, 'r') as f:
                content = f.read()

            required_elements = [
                'HTTPServer',
                'HTML_TEMPLATE',
                'InstallerHandler',
                'def main()'
            ]

            all_found = True
            for element in required_elements:
                if element in content:
                    print_success(f"Found component: {element}")
                else:
                    print_error(f"Missing component: {element}")
                    all_found = False

            return all_found
        else:
            print_error("web_installer.py not found")
            return False

    except Exception as e:
        print_error(f"Web installer test failed: {str(e)}")
        return False

def test_docker_files():
    """Test Docker configuration"""
    print_header("Docker Configuration Test")

    try:
        base_path = Path(__file__).parent

        # Check Dockerfile
        dockerfile = base_path / 'Dockerfile'
        if dockerfile.exists():
            with open(dockerfile, 'r') as f:
                content = f.read()

            if 'FROM python' in content and 'COPY src/' in content:
                print_success("Dockerfile is valid")
            else:
                print_warning("Dockerfile may be incomplete")
        else:
            print_error("Dockerfile not found")
            return False

        # Check docker-compose.yml
        compose_file = base_path / 'docker-compose.yml'
        if compose_file.exists():
            with open(compose_file, 'r') as f:
                content = f.read()

            if 'version:' in content and 'services:' in content:
                print_success("docker-compose.yml is valid")
            else:
                print_warning("docker-compose.yml may be incomplete")
        else:
            print_error("docker-compose.yml not found")
            return False

        return True

    except Exception as e:
        print_error(f"Docker test failed: {str(e)}")
        return False

def generate_report(results):
    """Generate final report"""
    print_header("Verification Report")

    total = len(results)
    passed = sum(results.values())

    print(f"\nTotal Tests: {total}")
    print(f"Passed: {Colors.GREEN}{passed}{Colors.END}")
    print(f"Failed: {Colors.RED}{total - passed}{Colors.END}")
    print(f"Success Rate: {(passed/total*100):.1f}%\n")

    print("Test Results:")
    for test, result in results.items():
        status = f"{Colors.GREEN}PASS{Colors.END}" if result else f"{Colors.RED}FAIL{Colors.END}"
        print(f"  {test}: {status}")

    if passed == total:
        print(f"\n{Colors.GREEN}{Colors.BOLD}✓ All tests passed! The installer is ready for standalone use.{Colors.END}")
        return True
    else:
        print(f"\n{Colors.YELLOW}{Colors.BOLD}⚠ Some tests failed. Please fix the issues above.{Colors.END}")
        return False

def main():
    """Run all verification tests"""
    print(f"{Colors.BOLD}{Colors.CYAN}")
    print("╔═══════════════════════════════════════════════════════════╗")
    print("║   Meshtasticd Installer - Standalone Verification        ║")
    print("╚═══════════════════════════════════════════════════════════╝")
    print(f"{Colors.END}")

    results = {}

    # Run all tests
    results['Python Version'] = check_python_version()
    results['Dependencies'] = check_dependencies()
    results['File Structure'] = check_file_structure()
    results['Script Permissions'] = check_scripts_executable()
    results['Module Imports'] = test_import_main()
    results['CLI Help'] = test_cli_help()
    results['Web Installer'] = test_web_installer()
    results['Docker Files'] = test_docker_files()

    # Generate report
    success = generate_report(results)

    sys.exit(0 if success else 1)

if __name__ == '__main__':
    main()
