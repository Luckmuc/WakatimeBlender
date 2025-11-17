#!/usr/bin/env python3
"""
Test script to verify BOM handling in wakatime config
"""

import os
import sys

# Add the wakatime_blender directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'wakatime_blender'))

try:
    # Test the settings loading
    from wakatime_blender import settings
    
    print("Testing settings loading...")
    
    # Try to load settings
    settings.load()
    
    # Test getting API key
    api_key = settings.api_key()
    print(f"API Key (first 8 chars): {api_key[:8] if api_key else 'Not set'}")
    
    # Test getting API URL
    api_url = settings.api_server_url()
    print(f"API URL: {api_url}")
    
    print("Settings loaded successfully!")
    
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()