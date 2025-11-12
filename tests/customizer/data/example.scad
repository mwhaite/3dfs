/* [Customizer] Example fixture */

wall_thickness = 2;
segments = 12; // [3:1:24]
material = "plastic"; // ["plastic", "steel", "wood"]
use_logo = true;

module demo() {
    cube([wall_thickness, wall_thickness, wall_thickness]);
}
