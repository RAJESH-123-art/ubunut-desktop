"""
Installation verification manager for confirming successful app installs.
Includes multiple verification methods and proper error reporting.
"""

import subprocess
import time
import os
from typing import Dict, Any, Optional
from loguru import logger


class InstallationVerificationManager:
    """Handles verification of app installation with multiple methods."""
    
    def __init__(self):
        self.verification_history = []
    
    def verify_app_installed(self, app_name: str, package_name: str = None) -> Dict[str, Any]:
        """Verify app is installed multiple ways.
        
        Args:
            app_name: User-friendly name of the app (e.g., "Mumble")
            package_name: Command line package name (defaults to lowercase app_name)
            
        Returns:
            Dictionary with verification results:
            {
                'installed': bool,
                'methods': dict,  # Results from each verification method
                'details': str,   # Additional details
                'success_count': int,
            }
        """
        if package_name is None:
            package_name = app_name.lower()
            
        results = {
            'installed': False,
            'methods': {},
            'details': '',
            'success_count': 0,
            'timestamp': time.time(),
            'app_name': app_name,
            'package_name': package_name,
        }
        
        # Method 1: Check with command - which
        logger.info(f"[{app_name}] Verification method 1: which command")
        try:
            result = subprocess.run(['which', package_name], 
                                     capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                path = result.stdout.strip()
                results['methods']['which_command'] = {'success': True, 'path': path}
                results['success_count'] += 1
                logger.info(f"[{app_name}] ✅ which command SUCCESS: {path}")
            else:
                results['methods']['which_command'] = {'success': False, 'error': 'Command not found in PATH'}
                logger.warning(f"[{app_name}] ❌ which command FAILED")
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
            results['methods']['which_command'] = {'success': False, 'error': str(e)}
            logger.warning(f"[{app_name}] ❌ which command ERROR: {e}")
        
        # Method 2: Check with dpkg/snap
        logger.info(f"[{app_name}] Verification method 2: package managers (dpkg/snap)")
        try:
            # Try dpkg first
            result = subprocess.run(['dpkg', '-l'], 
                                     capture_output=True, text=True, timeout=5)
            if result.returncode == 0 and package_name in result.stdout:
                results['methods']['dpkg_check'] = {'success': True}
                results['success_count'] += 1
                logger.info(f"[{app_name}] ✅ dpkg check SUCCESS")
            else:
                results['methods']['dpkg_check'] = {'success': False, 'error': 'Not installed via dpkg'}
                logger.warning(f"[{app_name}] ❌ dpkg check FAILED")
                
                # Try snap as fallback
                result = subprocess.run(['snap', 'list'], 
                                         capture_output=True, text=True, timeout=5)
                if result.returncode == 0 and package_name in result.stdout:
                    results['methods']['snap_check'] = {'success': True}
                    results['success_count'] += 1
                    logger.info(f"[{app_name}] ✅ snap check SUCCESS")
                else:
                    results['methods']['snap_check'] = {'success': False, 'error': 'Not installed via snap'}
                    logger.warning(f"[{app_name}] ❌ snap check FAILED")
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
            results['methods']['package_check'] = {'success': False, 'error': str(e)}
            logger.warning(f"[{app_name}] ❌ package check ERROR: {e}")
        
        # Method 3: Check if app can be executed
        logger.info(f"[{app_name}] Verification method 3: executable check")
        try:
            # Try running the app with --version or --help to see if it exists
            for check_arg in ['--version', '--help', '-h']:
                try:
                    result = subprocess.run([package_name, check_arg], 
                                             capture_output=True, text=True, timeout=5)
                    # App exists if it returns 0, or doesn't return 127 (command not found)
                    if result.returncode == 0 or (result.returncode != 127 and result.returncode != 1):
                        results['methods']['executable_check'] = {'success': True, 'arg': check_arg}
                        results['success_count'] += 1
                        logger.info(f"[{app_name}] ✅ executable check SUCCESS with {check_arg}")
                        break
                except FileNotFoundError:
                    results['methods']['executable_check'] = {'success': False, 'error': 'Executable not found'}
                    logger.warning(f"[{app_name}] ❌ executable check FAILED")
                    break
                except (subprocess.TimeoutExpired, Exception) as e:
                    # Continue to next check argument
                    logger.debug(f"[{app_name}] executable check arg {check_arg} failed: {e}")
                    continue
            
            if 'executable_check' not in results['methods']:
                results['methods']['executable_check'] = {'success': False, 'error': 'No working check argument'}
                logger.warning(f"[{app_name}] ❌ executable check FAILED")
        except Exception as e:
            results['methods']['executable_check'] = {'success': False, 'error': str(e)}
            logger.warning(f"[{app_name}] ❌ executable check ERROR: {e}")
        
        # Determine overall success (at least one method succeeded)
        results['installed'] = results['success_count'] > 0
        results['details'] = f"Verified via {results['success_count']}/3 methods"
        
        # Log the results
        if results['installed']:
            logger.info(f"✅ Verification SUCCEEDED for {app_name}: {results['details']}")
        else:
            logger.error(f"❌ Verification FAILED for {app_name}: {results['details']}")
        
        # Store in history for debugging
        self.verification_history.append(results)
        
        return results
    
    def check_running_processes(self, app_name: str, package_name: str = None) -> Dict[str, Any]:
        """Check if app process is running."""
        if package_name is None:
            package_name = app_name.lower()
        
        try:
            result = subprocess.run(['ps', 'aux'], capture_output=True, text=True, timeout=5)
            running = False
            
            if result.returncode == 0:
                if app_name.lower() in result.stdout.lower() or package_name in result.stdout.lower():
                    running = True
                    logger.info(f"✅ {app_name} process is RUNNING")
                else:
                    logger.warning(f"❌ {app_name} process NOT FOUND in ps output")
            
            return {'running': running}
        except Exception as e:
            logger.error(f"Failed to check process status for {app_name}: {e}")
            return {'running': False, 'error': str(e)}
    
    def get_verification_report(self) -> str:
        """Generate a report of all verifications performed."""
        report = "\n=== Installation Verification Report ===\n"
        for i, result in enumerate(self.verification_history, 1):
            report += f"\n{i}. App: {result['app_name']} (package: {result['package_name']})\n"
            report += f"   Status: {'✅ INSTALLED' if result['installed'] else '❌ NOT INSTALLED'}\n"
            report += f"   Details: {result['details']}\n"
            report += f"   Methods:\n"
            for method, detail in result['methods'].items():
                status = "✅ SUCCESS" if detail.get('success') else "❌ FAILED"
                report += f"      - {method}: {status}\n"
                if 'error' in detail:
                    report += f"        Error: {detail['error']}\n"
                if 'path' in detail:
                    report += f"        Path: {detail['path']}\n"
        
        return report