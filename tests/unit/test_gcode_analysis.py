#!/usr/bin/env python3
"""Test script to verify the G-code analysis still works with the fix."""

import tempfile
from pathlib import Path

from three_dfs.gcode.preview import GCodePreviewError, analyze_gcode_program


def test_gcode_analysis():
    """Test that the G-code analysis works with the new regex."""

    # Test case 1: Problematic format with decimal-only numbers
    gcode1 = """G21
G0 X.5 Y.5
G1 X1.5 Y1.5 F1200
G0 X2.
"""

    # Test case 2: Mix of different number formats
    gcode2 = """
G21 ; millimeters
G0 X0 Y0 Z5
G1 X.5 Y.5 Z.2
G1 X2. Y3. Z0.5
G1 X10 Y10
M5
M2
"""

    test_cases = [("decimal_only.gcode", gcode1), ("mixed_formats.gcode", gcode2)]

    print("Testing G-code analysis with the regex fix...")

    for filename, gcode_content in test_cases:
        print(f"\nTesting: {filename}")
        print(f"G-code content:\n{gcode_content}")

        # Write to temporary file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".gcode", delete=False) as f:
            f.write(gcode_content)
            temp_path = Path(f.name)

        try:
            # Analyze the G-code
            analysis = analyze_gcode_program(temp_path)
            print("✓ Analysis successful!")
            print(f"  - Total commands: {analysis.command_count}")
            print(f"  - Rapid moves: {analysis.rapid_moves}")
            print(f"  - Cutting moves: {analysis.cutting_moves}")
            print(f"  - Total moves: {analysis.total_moves}")
            print(f"  - Travel distance: {analysis.travel_distance:.2f}")
            print(f"  - Cutting distance: {analysis.cutting_distance:.2f}")
            print(f"  - X bounds: {analysis.bounds_xy[0]:.2f} to {analysis.bounds_xy[2]:.2f}")
            print(f"  - Y bounds: {analysis.bounds_xy[1]:.2f} to {analysis.bounds_xy[3]:.2f}")
            print(f"  - Z bounds: {analysis.bounds_z[0]:.2f} to {analysis.bounds_z[1]:.2f}")
            print(f"  - Feed rates: {analysis.feed_rates}")
            print(f"  - Units: {analysis.units}")
        except GCodePreviewError as e:
            print(f"✗ Analysis failed: {e}")
        except Exception as e:
            print(f"✗ Unexpected error: {e}")
        finally:
            # Clean up
            temp_path.unlink()

    # Test that the original problem reported is fixed
    print("\n" + "=" * 60)
    print("TESTING THE ORIGINAL PROBLEM CASES:")

    # This would have failed with the old regex - commands with .5 format
    problematic_gcode = """G1 X.5 Y.5
G1 X2.
G1 X10 Y10
"""

    print(f"G-code that previously failed: \n{problematic_gcode}")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".gcode", delete=False) as f:
        f.write(problematic_gcode)
        temp_path = Path(f.name)

    try:
        analysis = analyze_gcode_program(temp_path)
        print("✓ SUCCESS: Previously failing G-code now analyzes correctly!")
        print(f"  - Total moves detected: {analysis.total_moves}")
        print(f"  - Cutting moves: {analysis.cutting_moves}")
        print(f"  - Segments created: {len(analysis.segments)}")
    except GCodePreviewError as e:
        print(f"✗ STILL FAILING: {e}")
    except Exception as e:
        print(f"✗ Unexpected error: {e}")
    finally:
        temp_path.unlink()


if __name__ == "__main__":
    test_gcode_analysis()
