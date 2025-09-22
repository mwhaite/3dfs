/* [Customizer] STL embossing utility

   This utility script loads an existing STL file (or a generated plate) and
   embosses text or a 2D design onto its surface.  Parameters exposed through
   the OpenSCAD customizer make it easy to adjust the position, rotation, and
   depth of the embellishment.
*/

// -----------------------------------------------------------------------------
// Base geometry parameters
// -----------------------------------------------------------------------------
use_builtin_plate = true;
base_model = ""; // Path to an STL that should receive the embossing.
import_convexity = 10; // [1:1:20]

plate_length = 80; // [30:1:220]
plate_width = 40; // [20:1:180]
plate_thickness = 3; // [1:0.2:8]
plate_corner_radius = 6; // [0:0.5:20]
plate_segments = 64; // [12:4:128]

// -----------------------------------------------------------------------------
// Embossing behaviour
// -----------------------------------------------------------------------------
emboss_mode = "raised"; // ["raised", "recessed"]
plane_z = plate_thickness; // [-10:0.5:30]
engrave_depth = 0.8; // [0.2:0.1:3]

// -----------------------------------------------------------------------------
// Lettering controls
// -----------------------------------------------------------------------------
apply_text = true;
text_content = "Maker Lab";
text_font = "Liberation Sans:style=Bold";
text_size = 12; // [4:1:48]
text_thickness = 1.2; // [0.4:0.1:4]
text_spacing = 1.0; // [0.5:0.1:2.0]
text_halign = "center"; // ["left", "center", "right"]
text_valign = "center"; // ["baseline", "bottom", "center", "top"]
text_offset_x = 0; // [-120:1:120]
text_offset_y = 0; // [-120:1:120]
text_rotation = 0; // [-180:5:180]

// -----------------------------------------------------------------------------
// 2D design controls
// -----------------------------------------------------------------------------
apply_design = false;
design_file = ""; // Optional SVG/DXF/2D import for embossing.
design_scale = 1; // [0.1:0.1:5]
design_thickness = 1.2; // [0.4:0.1:4]
design_rotation = 0; // [-180:5:180]
design_offset_x = 0; // [-120:1:120]
design_offset_y = 0; // [-120:1:120]
design_mirror_x = false;
design_mirror_y = false;

// -----------------------------------------------------------------------------
// Output assembly
// -----------------------------------------------------------------------------
module base_geometry() {
    if (!use_builtin_plate && base_model != "") {
        import(base_model, convexity = import_convexity);
    } else {
        builtin_plate();
    }
}

module builtin_plate() {
    r = clamp_radius(plate_corner_radius, plate_length, plate_width);
    lx = max(plate_length - 2 * r, 0.1);
    ly = max(plate_width - 2 * r, 0.1);
    linear_extrude(height = plate_thickness, center = false, convexity = import_convexity)
        offset(r = r, $fn = plate_segments)
            square([lx, ly], center = true);
}

module emboss_shapes(mode) {
    if (apply_text && text_content != "") {
        text_shape(mode);
    }
    if (apply_design && design_file != "") {
        design_shape(mode);
    }
}

module text_shape(mode) {
    depth = mode == "raised" ? text_thickness : engrave_depth;
    z_pos = mode == "raised" ? plane_z : plane_z - depth;
    translate([text_offset_x, text_offset_y, z_pos])
        rotate([0, 0, text_rotation])
            linear_extrude(height = depth, center = false, convexity = import_convexity)
                text(
                    text_content,
                    size = text_size,
                    font = text_font,
                    halign = text_halign,
                    valign = text_valign,
                    spacing = text_spacing
                );
}

module design_shape(mode) {
    depth = mode == "raised" ? design_thickness : engrave_depth;
    z_pos = mode == "raised" ? plane_z : plane_z - depth;
    translate([design_offset_x, design_offset_y, z_pos])
        rotate([0, 0, design_rotation])
            linear_extrude(height = depth, center = false, convexity = import_convexity)
                transformed_design_profile();
}

module transformed_design_profile() {
    if (design_mirror_x && design_mirror_y) {
        mirror([1, 0, 0]) mirror([0, 1, 0]) scaled_design_profile();
    } else if (design_mirror_x) {
        mirror([1, 0, 0]) scaled_design_profile();
    } else if (design_mirror_y) {
        mirror([0, 1, 0]) scaled_design_profile();
    } else {
        scaled_design_profile();
    }
}

module scaled_design_profile() {
    scale([design_scale, design_scale, 1])
        import(design_file, convexity = import_convexity);
}

module emboss_assembly() {
    if (emboss_mode == "recessed") {
        difference() {
            base_geometry();
            emboss_shapes("recessed");
        }
    } else {
        union() {
            base_geometry();
            emboss_shapes("raised");
        }
    }
}

function clamp_radius(radius, length, width) =
    radius <= 0 ? 0 : min(radius, min(length, width) / 2 - 0.01);

// Render the final model
emboss_assembly();
