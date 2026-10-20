# Non-project Vivado build of fpga_top for the Nexys 4 DDR.
#   vivado -mode batch -source build.tcl -tclargs <latch|ff> <baud>
# Run from the output folder (fpga.ps1 build does this). Writes fpga_top_<ram>.bit and reports.
set ram  [lindex $argv 0]
set baud [lindex $argv 1]
set part xc7a100tcsg324-1
set fpga [file normalize [file join [file dirname [info script]] ..]]

set chip [list project.v perceptron_704.v predictors.v]
set src {}
foreach f $chip { lappend src [file join $fpga .. rtl $f] }
if {$ram eq "ff"} {
    lappend src [file join $fpga hdl bp_latch_mem_ff.v]
} else {
    # behavioural latches: without SIM, latch_memory.v instantiates sky130 cells
    lappend src [file join $fpga .. rtl latch_memory.v]
}
foreach f {uart_rx.v uart_tx.v sync_fifo.v bp_hil_core.v board_ui.v fpga_top.v} { lappend src [file join $fpga hdl $f] }

read_verilog $src
read_xdc [list [file join $fpga constraints nexys4ddr_bp.xdc]]

synth_design -top fpga_top -part $part -verilog_define SIM -generic BAUD=$baud
report_utilization -file post_synth_util.rpt
write_checkpoint -force post_synth.dcp
# functional netlist for post-synthesis simulation (checks what Vivado made of the latch RAM)
write_verilog -force -mode funcsim post_synth_funcsim.v

opt_design
place_design
phys_opt_design
route_design
write_checkpoint -force post_route.dcp

report_utilization -file util.rpt
report_utilization -hierarchical -file util_hier.rpt
report_timing_summary -max_paths 20 -file timing.rpt
report_methodology -file methodology.rpt
report_drc -file drc.rpt

set wns [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -setup]]
set whs [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -hold]]
set latches [llength [get_cells -hier -filter {REF_NAME =~ LD*}]]
set ffs     [llength [get_cells -hier -filter {REF_NAME =~ FD*}]]
set luts    [llength [get_cells -hier -filter {REF_NAME =~ LUT*}]]
puts "BUILD ram=$ram baud=$baud WNS=$wns WHS=$whs LUTs=$luts FFs=$ffs latches=$latches"
if {$wns < 0 || $whs < 0} { puts "BUILD TIMING FAILED"; exit 1 }

write_bitstream -force fpga_top_$ram.bit
puts "BUILD OK fpga_top_$ram.bit"
