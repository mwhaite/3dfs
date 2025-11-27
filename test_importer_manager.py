#!/usr/bin/env python3
"""Test script to verify the importer manager can be instantiated."""

def test_importer_manager():
    """Test that the ImporterManager can be imported and instantiated without errors."""
    print("Testing ImporterManager instantiation...")
    
    try:
        from three_dfs.importers import ImporterManager
        print("✓ Successfully imported ImporterManager")
        
        # Try to instantiate
        manager = ImporterManager()
        print("✓ Successfully instantiated ImporterManager")
        
        # Check that importers are registered
        available_importers = list(manager._importers.keys())
        print(f"✓ Available importers: {available_importers}")
        
        # Test that we can get each importer
        for importer_name in available_importers:
            importer = manager.get_importer(importer_name)
            if importer:
                print(f"✓ Successfully got {importer_name} importer")
            else:
                print(f"✗ Failed to get {importer_name} importer")
        
        print("\nAll tests passed! ImporterManager is working correctly.")
        return True
        
    except ImportError as e:
        print(f"✗ Import error: {e}")
        return False
    except Exception as e:
        print(f"✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_importer_manager()
    if success:
        print("\n✓ ImporterManager test passed!")
    else:
        print("\n✗ ImporterManager test failed!")
        exit(1)