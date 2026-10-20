# Write the routed design as a timing-simulation netlist with SDF delays.
#   vivado -mode batch -source timesim_netlist.tcl -tclargs <post_route.dcp> <out dir>
# Static timing cannot check the latch RAM (its latch enables come from logic: TIMING-20), so the latch build is
# also simulated with the real post-route delays.
set dcp [lindex $argv 0]
set out [lindex $argv 1]
open_checkpoint $dcp
write_verilog -force -mode timesim -sdf_anno true -sdf_file [file join $out fpga_top_timesim.sdf] [file join $out fpga_top_timesim.v]
write_sdf -force -process_corner slow [file join $out fpga_top_timesim.sdf]
puts "TIMESIM netlist written to $out"
