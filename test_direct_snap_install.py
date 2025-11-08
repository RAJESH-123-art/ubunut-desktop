#!/usr/bin/env python3
"""
Direct snap installation without GUI automation.
This is a more reliable approach for Wayland environments.
"""

import subprocess
import time
from loguru import logger
from tasks.install_verification import InstallationVerificationManager


def install_via_snap_cli(app_name: str, package_name: str):
    """Install app directly via snap CLI instead of GUI automation."""
    logger.info(f"Installing {app_name} via snap CLI...")
    
    try:
        # Check if already installed
        result = subprocess.run(['snap', 'list'], capture_output=True, text=True)
        if package_name in result.stdout:
            logger.info(f"{app_name} is already installed")
            return True
        
        # Install via snap
        logger.info(f"Running: sudo snap install {package_name}")
        result = subprocess.run(['sudo', 'snap', 'install', package_name], 
                                capture_output=True, text=True, timeout=300)
        
        if result.returncode == 0:
            logger.info(f"✅ {app_name} installation command completed successfully")
            logger.info(f"Output: {result.stdout}")
            return True
        else:
            logger.error(f"❌ {app_name} installation failed")
            logger.error(f"Error: {result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        logger.error(f"Installation timed out after 5 minutes")
        return False
    except Exception as e:
        logger.error(f"Installation error: {e}")
        return False


def main():
    """Install VLC and verify."""
    logger.info("="*60)
    logger.info("Direct Snap Installation Test")
    logger.info("="*60)
    
    app_name = "VLC"
    package_name = "vlc"
    
    # Install
    success = install_via_snap_cli(app_name, package_name)
    
    if not success:
        logger.error("Installation failed")
        return 1
    
    # Wait a bit for installation to settle
    time.sleep(5)
    
    # Verify
    logger.info("\nVerifying installation...")
    verifier = InstallationVerificationManager()
    results = verifier.verify_app_installed(app_name, package_name)
    
    # Print report
    report = verifier.get_verification_report()
    logger.info(report)
    
    if results['installed']:
        logger.info("\n" + "="*60)
        logger.info("✅ SUCCESS: VLC is installed and verified!")
        logger.info("="*60)
        
        # Move logs
        import os
        logs_dir = os.path.expanduser("~/logs")
        os.makedirs(logs_dir, exist_ok=True)
        
        log_sources = ["/var/log/apt/history.log", "/var/log/dpkg.log"]
        for log_file in log_sources:
            if os.path.exists(log_file):
                try:
                    dest = os.path.join(logs_dir, os.path.basename(log_file))
                    subprocess.run(["cp", log_file, dest], check=True)
                    logger.info(f"Copied {log_file} to {dest}")
                except Exception as e:
                    logger.warning(f"Could not copy {log_file}: {e}")
        
        return 0
    else:
        logger.error("\n" + "="*60)
        logger.error("❌ FAILED: VLC installation could not be verified")
        logger.error("="*60)
        return 1


if __name__ == "__main__":
    exit(main())
