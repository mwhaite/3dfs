#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, '/home/b/Projects/3dfs/src')

import logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

print("Starting GUI test...", flush=True)

try:
    from PySide6.QtWidgets import QApplication
    print("QtWidgets imported successfully", flush=True)

    from three_dfs.app import main
    print("Main function imported", flush=True)

    print("Calling main()...", flush=True)
    result = main()
    print(f"Main completed with result: {result}", flush=True)

except Exception as e:
    print(f"Exception occurred: {e}", flush=True)
    import traceback
    traceback.print_exc()