#!/usr/bin/env python3
"""Test script to verify the G-code regex pattern fix."""

import re
from pathlib import Path

# Current (fixed) pattern
_GCODE_WORD_PATTERN = re.compile(r"([A-Za-z])([-+]?(?:\d+\.?\d*|\.\d+))")

def test_regex():
    """Test the regex pattern with various G-code number formats."""
    
    test_cases = [
        # (input_string, expected_matches) - including the G command
        ("G1 X.5 Y.5", [("G", "1"), ("X", ".5"), ("Y", ".5")]),
        ("G1 X2.", [("G", "1"), ("X", "2.")]),
        ("G1 X2.5", [("G", "1"), ("X", "2.5")]),
        ("G1 X-2.5", [("G", "1"), ("X", "-2.5")]),
        ("G1 X+2.5", [("G", "1"), ("X", "+2.5")]),
        ("G1 X1 Y2.5 Z.75", [("G", "1"), ("X", "1"), ("Y", "2.5"), ("Z", ".75")]),
        ("G0 X.123 Y456.789", [("G", "0"), ("X", ".123"), ("Y", "456.789")]),
        ("G1 X2 Y3", [("G", "1"), ("X", "2"), ("Y", "3")]),
    ]
    
    print("Testing G-code word pattern...")
    
    all_passed = True
    for i, (input_str, expected) in enumerate(test_cases):
        matches = _GCODE_WORD_PATTERN.findall(input_str)
        print(f"Test {i+1}: '{input_str}'")
        print(f"  Expected: {expected}")
        print(f"  Got:      {matches}")
        print(f"  Status:   {'PASS' if matches == expected else 'FAIL'}")
        print()
        if matches != expected:
            all_passed = False
    
    if all_passed:
        print("All tests passed! The regex fix is working correctly.")
    else:
        print("Some tests failed.")
        
    # Test that the specific problematic formats now work (focus on X/Y/Z coordinates)
    print("\nTesting that problematic decimal formats are now captured:")
    test_lines = [
        "G1 X.5 Y.5",
        "G1 X2.",
        "G1 X.5 Y2. Z3.5",
    ]
    
    for line in test_lines:
        matches = _GCODE_WORD_PATTERN.findall(line)
        # Extract just the coordinates (X, Y, Z)
        coords = [(letter, value) for letter, value in matches if letter.upper() in 'XYZ']
        print(f"  '{line}' -> Coordinates: {coords}")
        # Check if coordinates were properly captured
        if coords:
            print(f"    ✓ Successfully captured coordinate values: {[v for _, v in coords]}")
        else:
            print(f"    ✗ Failed to capture coordinates!")

if __name__ == "__main__":
    test_regex()