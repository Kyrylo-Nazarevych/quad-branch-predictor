# Load a bitstream into the Nexys 4 DDR over the on-board USB-JTAG (micro-USB J6, labelled PROG).
#   vivado -mode batch -source program.tcl -tclargs <file.bit>
# Volatile: the FPGA forgets it at power-off. JTAG works whatever the JP1 mode jumper says.
set bit [lindex $argv 0]
open_hw_manager
connect_hw_server -allow_non_jtag
set targets [get_hw_targets *]
if {[llength $targets] == 0} { puts "PROGRAM ERROR: no JTAG target. Is the board plugged into J6 and switched on?"; exit 1 }
puts "PROGRAM target [lindex $targets 0]"
open_hw_target [lindex $targets 0]
set dev [lindex [get_hw_devices xc7a100t*] 0]
if {$dev eq ""} { puts "PROGRAM ERROR: no xc7a100t on the chain: [get_hw_devices]"; exit 1 }
current_hw_device $dev
set_property PROGRAM.FILE $bit $dev
program_hw_devices $dev
refresh_hw_device $dev
set done [get_property REGISTER.CONFIG_STATUS.BIT14_DONE_PIN $dev]
puts "PROGRAM done_pin=$done file=$bit"
close_hw_target
disconnect_hw_server
close_hw_manager
if {$done != 1} { puts "PROGRAM ERROR: DONE pin not high"; exit 1 }
puts "PROGRAM OK"
