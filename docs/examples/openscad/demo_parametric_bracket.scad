/* [Customizer] Parametric corner bracket demonstration

   A printable 90° bracket showcasing the OpenSCAD customizer workflow.  The
   script exposes parameters for leg dimensions, mounting holes, and gusset
   styles so users can experiment with a practical part.
*/

// -----------------------------------------------------------------------------
// Dimension controls
// -----------------------------------------------------------------------------
leg_length_x = 70; // [40:1:180]
leg_length_y = 50; // [30:1:160]
leg_width = 24; // [12:1:60]
leg_thickness = 5; // [2:0.5:12]
fillet_radius = 4; // [0:0.5:18]
curve_segments = 48; // [12:4:120]

// -----------------------------------------------------------------------------
// Gusset configuration
// -----------------------------------------------------------------------------
brace_style = "solid"; // ["solid", "lightweight", "none"]
brace_length = 40; // [10:1:160]
brace_width = 16; // [6:1:40]

// -----------------------------------------------------------------------------
// Mounting hole layout
// -----------------------------------------------------------------------------
hole_diameter = 5; // [3:0.5:12]
hole_wall = 2; // [1:0.5:6]
primary_hole_offset = 30; // [10:1:170]
secondary_hole_offset = 55; // [15:1:180]
use_secondary_hole = true;

// -----------------------------------------------------------------------------
// Project
// -----------------------------------------------------------------------------
module corner_bracket() {
    difference() {
        union() {
            leg_body(leg_length_x, leg_width);
            leg_body(leg_width, leg_length_y);
            if (brace_style != "none") {
                gusset();
            }
        }
        drill_leg_holes(leg_length_x, leg_width, "x");
        drill_leg_holes(leg_length_y, leg_width, "y");
    }
}

module leg_body(axis_length, axis_depth) {
    r = clamp_leg_radius(fillet_radius, axis_length, axis_depth);
    if (r <= 0) {
        linear_extrude(height = leg_thickness, center = false)
            square([axis_length, axis_depth], center = false);
    } else {
        linear_extrude(height = leg_thickness, center = false, convexity = 4)
            hull() {
                square([r, axis_depth], center = false);
                square([axis_length, r], center = false);
                translate([axis_length - r, axis_depth - r, 0])
                    circle(r = r, $fn = curve_segments);
            }
    }
}

module gusset() {
    span = min(brace_length, min(leg_length_x, leg_length_y));
    width = min(brace_width, min(leg_width, span));
    if (span <= 0 || width <= 0) {
        return;
    }
    if (brace_style == "solid") {
        gusset_block(span, width);
    } else if (brace_style == "lightweight") {
        difference() {
            gusset_block(span, width);
            gusset_cutout(span, width);
        }
    }
}

module gusset_block(span, width) {
    hull() {
        cube([width, span, leg_thickness], center = false);
        cube([span, width, leg_thickness], center = false);
    }
}

module gusset_cutout(span, width) {
    cut_center = width + (span - width) / 2;
    cut_radius = max(width * 0.3, 0.5);
    translate([cut_center, cut_center, -0.2])
        cylinder(r = cut_radius, h = leg_thickness + 0.4, center = false, $fn = curve_segments);
}

module drill_leg_holes(length, width, axis = "x") {
    margin = hole_diameter / 2 + hole_wall;
    if (hole_diameter <= 0 || length <= margin * 2) {
        return;
    }
    for (offset_value = hole_offset_list(length, margin)) {
        if (axis == "x") {
            translate([offset_value, width / 2, -0.1])
                cylinder(d = hole_diameter, h = leg_thickness + 0.2, center = false, $fn = curve_segments);
        } else {
            translate([width / 2, offset_value, -0.1])
                cylinder(d = hole_diameter, h = leg_thickness + 0.2, center = false, $fn = curve_segments);
        }
    }
}

function hole_offset_list(length, margin) = use_secondary_hole
    ? [clamp(primary_hole_offset, margin, length - margin),
       clamp(secondary_hole_offset, margin, length - margin)]
    : [clamp(primary_hole_offset, margin, length - margin)];

function clamp_leg_radius(value, max_length, max_width) = value <= 0
    ? 0
    : min(value, min(max_length, max_width) / 2 - 0.01);

function clamp(value, lower, upper) = min(max(value, lower), upper);

// Render the bracket
corner_bracket();
