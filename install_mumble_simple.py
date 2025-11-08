#!/usr/bin/env python3

# Simple script to install Mumble using the enhanced desktop automation framework with verification
from tasks.install_app import setup, execute, cleanup

def main():
    # Prepare arguments for Mumble installation
    args = {
        "app_name": "Mumble",
        "package_name": "mumble",  # Override command name for verification
        "password": "rgukt"  # Update this to your actual password
    }
    
    print("Starting Mumble installation with enhanced verification...")
    print(f"This will attempt to install {args['app_name']} through the App Center")
    print("You'll need to provide your system password when prompted.")
    print("The script will verify that Mumble is actually installed.")
    
    # Setup resources
    resources = setup()
    
    try:
        # Execute the installation
        execute(args, resources)
        print("Mumble installation process completed successfully!")
    except Exception as e:
        print(f"Error during installation: {e}")
        return 1
    finally:
        # Clean up resources
        cleanup(resources)
    
    return 0

if __name__ == "__main__":
    exit(main())