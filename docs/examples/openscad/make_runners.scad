/* [Customizer] Assembly runner plate generator

   This OpenSCAD utility arranges the components of a multi-material assembly
   onto runner plates reminiscent of old style plastic model kits. Components
   are grouped by material and colour so each plate can be printed in a single
   filament or resin. The script produces rectangular frames, a primary sprue,
   gates for every component, and optional labels that identify each part.
*/

// -----------------------------------------------------------------------------
// Build volume configuration
// -----------------------------------------------------------------------------
build_plate_preset = "Bambu X1 (256x256)"; // ["Prusa MK3 (250x210)", "Bambu X1 (256x256)", "Creality Ender 3 (220x220)", "Elegoo Neptune 3 (235x235)", "Voron Trident 300 (300x300)", "Formlabs Fuse 1 (165x165)", "Custom"]
custom_plate_length = 260; // [120:1:400]
custom_plate_width = 260; // [120:1:400]
plate_thickness = 2.6; // [1.2:0.1:6]
runner_depth = 2.6; // [1.2:0.1:6]
frame_wall = 5.0; // [3:0.5:14]
runner_width = 3.2; // [1.5:0.1:6]
gate_width = 6.0; // [2:0.5:10]
gate_height = 3.0; // [1:0.2:6]
part_spacing = 10; // [4:0.5:24]
frame_margin = 16; // [8:0.5:40]
plates_per_row = 2; // [1:1:4]
plate_gap = 40; // [20:5:80]

// -----------------------------------------------------------------------------
// Grouping and preview controls
// -----------------------------------------------------------------------------
group_mode = "Material + color"; // ["Material", "Material + color"]
render_mode = "All plates"; // ["All plates", "Single plate"]
selected_plate = 0; // [0:1:12]

label_parts = true;
label_font = "Liberation Sans:style=Bold";
label_font_size = 6; // [3:0.5:14]
label_thickness = 0.6; // [0.2:0.1:1.6]
show_group_labels = true;
group_label_font_size = 9; // [4:0.5:20]
group_label_offset = 10; // [4:0.5:30]

// -----------------------------------------------------------------------------
// Assembly description
// -----------------------------------------------------------------------------
// Each entry: [name, material, colour, [size_x, size_y, size_z], shape_id, rotation_deg]
assembly_parts = [
    ["fuselage_upper", "ABS", "Slate Gray", [92, 36, 18], "shell", 0],
    ["fuselage_lower", "ABS", "Slate Gray", [88, 34, 16], "shell", 0],
    ["canopy_frame", "PETG", "Carbon Black", [50, 28, 10], "frame", 0],
    ["electronics_plate", "PETG", "Carbon Black", [54, 38, 4], "panel", 90],
    ["main_blade_left", "PLA", "Polar White", [120, 18, 4], "blade", 0],
    ["main_blade_right", "PLA", "Polar White", [120, 18, 4], "blade_mirror", 0],
    ["tail_fin", "PLA", "Signal Red", [62, 36, 5], "fin", 0],
    ["tail_boom", "PLA", "Signal Red", [140, 16, 12], "boom", 0],
    ["landing_skid_left", "Nylon", "Graphite", [110, 20, 12], "skid", 0],
    ["landing_skid_right", "Nylon", "Graphite", [110, 20, 12], "skid_mirror", 0]
];

// -----------------------------------------------------------------------------
// Constants and utility helpers
// -----------------------------------------------------------------------------
EPSILON = 0.01;

function deg2rad(angle) = angle * PI / 180;
function clamp(value, lo, hi) = value < lo ? lo : value > hi ? hi : value;

function part_name(part) = part[0];
function part_material(part) = part[1];
function part_colour(part) = part[2];
function part_size(part) = part[3];
function part_shape(part) = part[4];
function part_rotation(part) = len(part) > 5 ? part[5] : 0;

function part_layout_size(part) = rotated_size(part_size(part), part_rotation(part));
function rotated_size(size, angle) =
    let(
        a = deg2rad(angle),
        cx = abs(cos(a)),
        sx = abs(sin(a))
    ) [
        size[0] * cx + size[1] * sx,
        size[0] * sx + size[1] * cx,
        size[2]
    ];

function gate_overlap_amount(width) = clamp(width * 0.35, 0, width - 0.2);

function colour_rgb(name) =
    name == "Slate Gray" ? [0.43, 0.47, 0.51] :
    name == "Carbon Black" ? [0.05, 0.05, 0.05] :
    name == "Polar White" ? [0.93, 0.94, 0.95] :
    name == "Signal Red" ? [0.82, 0.18, 0.18] :
    name == "Graphite" ? [0.27, 0.28, 0.3] :
    [0.75, 0.75, 0.75];

function plate_size_from_preset(name) =
    name == "Prusa MK3 (250x210)" ? [250, 210] :
    name == "Bambu X1 (256x256)" ? [256, 256] :
    name == "Creality Ender 3 (220x220)" ? [220, 220] :
    name == "Elegoo Neptune 3 (235x235)" ? [235, 235] :
    name == "Voron Trident 300 (300x300)" ? [300, 300] :
    name == "Formlabs Fuse 1 (165x165)" ? [165, 165] :
    [custom_plate_length, custom_plate_width];

function plate_size() = plate_size_from_preset(build_plate_preset);

function group_tag_for_part(part) =
    group_mode == "Material + color" ? [part_material(part), part_colour(part)] : [part_material(part), "all"];

function contains_item(list, item, idx = 0) =
    idx >= len(list) ? false : (list[idx] == item ? true : contains_item(list, item, idx + 1));

function unique_groups(parts, idx = 0, acc = []) =
    idx >= len(parts)
        ? acc
        : let(tag = group_tag_for_part(parts[idx]))
            unique_groups(parts, idx + 1, contains_item(acc, tag) ? acc : concat(acc, [tag]));

function filter_parts_by_group(parts, tag) = [for (part = parts) if (group_tag_for_part(part) == tag) part];

function group_label(tag) =
    len(tag) < 2 || tag[1] == "all" ? str(tag[0]) : str(tag[0], " / ", tag[1]);

function layout_positions(parts, dims, spacing, margins, idx = 0, cursor = [margins[0], margins[1]], row_height = 0) =
    idx >= len(parts)
        ? []
        : let(
            part = parts[idx],
            size = part_layout_size(part),
            max_x = dims[0] - margins[0],
            max_y = dims[1] - margins[1],
            new_row = cursor[0] > margins[0] && cursor[0] + size[0] > max_x,
            position = new_row ? [margins[0], cursor[1] + row_height + spacing] : cursor,
            warn = position[1] + size[1] > max_y ? echo(str("Plate overflow: ", part_name(part))) : 0,
            center = [position[0] + size[0] / 2, position[1] + size[1] / 2],
            row_peak = new_row ? size[1] : max(row_height, size[1]),
            cursor_after = [position[0] + size[0] + spacing, position[1]],
            needs_wrap = cursor_after[0] > max_x,
            next_cursor = needs_wrap ? [margins[0], position[1] + row_peak + spacing] : cursor_after,
            next_row_height = needs_wrap ? 0 : row_peak
        )
        concat([center], layout_positions(parts, dims, spacing, margins, idx + 1, next_cursor, next_row_height));

function clamp_plate_index(index, count) = clamp(index, 0, count - 1);

// -----------------------------------------------------------------------------
// Geometry helpers
// -----------------------------------------------------------------------------
module rounded_box(size, radius) {
    max_radius = max(min(size[0], size[1]) / 2 - EPSILON, 0);
    r = clamp(radius, 0, max_radius);
    inner = [max(size[0] - 2 * r, EPSILON), max(size[1] - 2 * r, EPSILON), max(size[2] - 2 * r, EPSILON)];
    translate([0, 0, size[2] / 2])
        minkowski() {
            cube(inner, center = true);
            sphere(r = max(r, EPSILON), $fn = max(24, floor(6 * max(r, 1))));
        }
}

module hollow_shell(size, wall, radius) {
    difference() {
        rounded_box(size, radius);
        translate([0, 0, wall])
            rounded_box(
                [
                    max(size[0] - 2 * wall, EPSILON),
                    max(size[1] - 2 * wall, EPSILON),
                    max(size[2] - wall, EPSILON)
                ],
                max(radius - wall, EPSILON)
            );
    }
}

module panel_plate(size, radius) {
    linear_extrude(height = size[2], center = false, convexity = 6)
        offset(r = min(radius, min(size[0], size[1]) / 4), $fn = 36)
            square([max(size[0] - radius, EPSILON), max(size[1] - radius, EPSILON)], center = true);
}

module rotor_blade_shape() {
    linear_extrude(height = 1, center = false, twist = 10, convexity = 6)
        polygon(points = [
            [-0.5, -0.45],
            [0.5, -0.15],
            [0.5, 0.15],
            [-0.25, 0.5],
            [-0.5, 0.2],
            [-0.5, -0.2]
        ]);
}

module rotor_blade(size) {
    scale([size[0], size[1], size[2]])
        rotor_blade_shape();
}

module tail_fin_shape(size) {
    linear_extrude(height = size[2], center = false, convexity = 6)
        polygon(points = [
            [-size[0] / 2, -size[1] / 2],
            [size[0] / 2, -size[1] / 4],
            [size[0] / 4, size[1] / 2],
            [-size[0] / 2, size[1] / 4]
        ]);
}

module boom_prism(size) {
    hull() {
        translate([-size[0] / 2, 0, size[2] / 2])
            scale([size[1] / 3, size[1] / 3, size[2] / 2]) sphere(r = 1, $fn = 32);
        translate([size[0] / 2, 0, size[2] / 2])
            scale([size[1] / 3, size[1] / 3, size[2] / 2]) sphere(r = 1, $fn = 32);
    }
}

module skid_shape(size, mirrored = false) {
    rail_radius = max(size[2] * 0.25, EPSILON);
    rail_offset = (mirrored ? -1 : 1) * (size[1] / 2 - rail_radius);
    // Base rail hugging the build plane
    hull() {
        translate([-size[0] / 2, rail_offset, rail_radius]) sphere(r = rail_radius, $fn = 32);
        translate([size[0] / 2, rail_offset, rail_radius]) sphere(r = rail_radius, $fn = 32);
    }
    // Struts that lean towards the fuselage centerline
    for (sign = [-1, 1])
        hull() {
            translate([sign * size[0] / 3, rail_offset, rail_radius * 1.2]) sphere(r = rail_radius * 0.7, $fn = 28);
            translate([sign * size[0] / 4, 0, size[2]]) sphere(r = rail_radius * 0.6, $fn = 28);
        }
}

module part_geometry(shape_id, size) {
    if (shape_id == "shell") {
        hollow_shell(size, wall = max(size[2] * 0.18, 1.2), radius = min(size[2] * 0.6, min(size[0], size[1]) / 3));
    } else if (shape_id == "frame") {
        difference() {
            hollow_shell(size, wall = max(size[2] * 0.2, 1.0), radius = min(size[2] * 0.5, min(size[0], size[1]) / 3));
            translate([0, 0, size[2] * 0.6])
                rounded_box([size[0] * 0.4, size[1] * 0.6, size[2]], min(size[2] * 0.2, min(size[0], size[1]) / 4));
        }
    } else if (shape_id == "panel") {
        panel_plate(size, radius = min(size[2] * 1.5, 6));
    } else if (shape_id == "blade") {
        rotor_blade(size);
    } else if (shape_id == "blade_mirror") {
        mirror([0, 1, 0]) rotor_blade(size);
    } else if (shape_id == "fin") {
        tail_fin_shape(size);
    } else if (shape_id == "boom") {
        boom_prism(size);
    } else if (shape_id == "skid") {
        skid_shape(size, mirrored = false);
    } else if (shape_id == "skid_mirror") {
        skid_shape(size, mirrored = true);
    } else {
        rounded_box(size, radius = min(size[2] * 0.3, min(size[0], size[1]) / 4));
    }
}

// -----------------------------------------------------------------------------
// Runner construction
// -----------------------------------------------------------------------------
module runner_frame(dimensions, wall, height) {
    length = dimensions[0];
    width = dimensions[1];
    if (length <= 2 * wall || width <= 2 * wall) {
        translate([0, 0, height / 2])
            cube([length, width, height], center = true);
    } else {
        difference() {
            translate([0, 0, height / 2]) cube([length, width, height], center = true);
            translate([0, 0, height / 2])
                cube([length - 2 * wall, width - 2 * wall, height + EPSILON], center = true);
        }
    }
}

module sprue_column(length, width, wall, runner_w, runner_h) {
    translate([-length / 2 + wall + runner_w / 2, 0, runner_h / 2])
        cube([runner_w, width - 2 * wall, runner_h], center = true);
}

module connector_to_part(start_x, center_y, length, runner_h) {
    translate([start_x + length / 2, center_y, runner_h / 2])
        cube([length, runner_width, runner_h], center = true);
}

module gate_into_part(start_x, center_y, gate_w, gate_h, runner_h) {
    translate([start_x + gate_w / 2, center_y, runner_h - gate_h / 2])
        cube([gate_w, runner_width, gate_h], center = true);
}

module part_label_marker(text_value, position, height) {
    if (label_parts && text_value != "")
        translate([position[0], position[1], height])
            linear_extrude(height = label_thickness, center = false, convexity = 4)
                text(text_value, size = label_font_size, font = label_font, halign = "center", valign = "bottom");
}

module group_label_marker(text_value, plate_dims, height) {
    if (show_group_labels && text_value != "")
        translate([0, -plate_dims[1] / 2 - group_label_offset, height])
            linear_extrude(height = label_thickness, center = false, convexity = 4)
                text(text_value, size = group_label_font_size, font = label_font, halign = "center", valign = "top");
}

module runner_plate(plate_index, tag, parts, plate_dims) {
    length = plate_dims[0];
    width = plate_dims[1];
    frame_height = plate_thickness;
    runner_h = max(runner_depth, frame_height);
    gate_h = gate_height;
    margin_x = max(frame_margin, frame_wall + runner_width + gate_width + 4);
    margin_y = frame_margin;
    positions = layout_positions(parts, [length, width], part_spacing, [margin_x, margin_y]);

    runner_frame([length, width, frame_height], frame_wall, frame_height);
    sprue_column(length, width, frame_wall, runner_width, runner_h);
    group_label_marker(str(group_label(tag), " (#", plate_index + 1, ")"), plate_dims, runner_h + label_thickness / 2);

    translate([-length / 2, -width / 2, 0]) {
        sprue_edge = frame_wall + runner_width;
        for (idx = [0 : len(parts) - 1]) {
            part = parts[idx];
            pos = positions[idx];
            layout_size = part_layout_size(part);
            actual_size = part_size(part);
            center = [pos[0], pos[1]];
            part_colour_value = colour_rgb(part_colour(part));
            rot = part_rotation(part);
            gate_overlap = gate_overlap_amount(gate_width);
            gate_base_x = center[0] - layout_size[0] / 2 - gate_width + gate_overlap;
            runner_length = gate_base_x - sprue_edge;
            gate_use_height = min(gate_h, actual_size[2], runner_h);
            if (runner_length <= 0)
                echo(str("Runner too short for part: ", part_name(part)));
            else
                connector_to_part(sprue_edge, center[1], runner_length, runner_h);
            gate_into_part(gate_base_x, center[1], gate_width, gate_use_height, runner_h);
            color(part_colour_value)
                translate([center[0], center[1], runner_h])
                    rotate([0, 0, rot])
                        part_geometry(part_shape(part), actual_size);
            part_label_marker(part_name(part), [center[0], center[1]], runner_h + actual_size[2] + 0.4);
        }
    }
}

// -----------------------------------------------------------------------------
// Layout orchestration
// -----------------------------------------------------------------------------
module make_runner_system() {
    dims = plate_size();
    plate_dims = [dims[0], dims[1]];
    groups = unique_groups(assembly_parts);
    count = len(groups);
    if (count == 0) {
        echo("Assembly contains no parts to arrange.");
    } else if (render_mode == "Single plate") {
        idx = clamp_plate_index(selected_plate, count);
        tag = groups[idx];
        parts = filter_parts_by_group(assembly_parts, tag);
        runner_plate(idx, tag, parts, [plate_dims[0], plate_dims[1]]);
    } else {
        cols = max(1, min(plates_per_row, count));
        rows = ceil(count / cols);
        total_width = (cols - 1) * (plate_dims[0] + plate_gap);
        total_height = (rows - 1) * (plate_dims[1] + plate_gap);
        origin_x = -total_width / 2;
        origin_y = total_height / 2;
        for (plate_index = [0 : count - 1]) {
            tag = groups[plate_index];
            parts = filter_parts_by_group(assembly_parts, tag);
            row = floor(plate_index / cols);
            col = plate_index % cols;
            offset_x = origin_x + col * (plate_dims[0] + plate_gap);
            offset_y = origin_y - row * (plate_dims[1] + plate_gap);
            translate([offset_x, offset_y, 0])
                runner_plate(plate_index, tag, parts, [plate_dims[0], plate_dims[1]]);
        }
    }
}

// Generate the runner plates
make_runner_system();
