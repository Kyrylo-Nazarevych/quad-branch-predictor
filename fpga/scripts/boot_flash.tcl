# Make the FPGA reload itself from the QSPI flash (as at power-on), without touching the flash.
#   vivado -mode batch -source boot_flash.tcl
# Needs the JP1 mode jumper on QSPI. JPROGRAM clears the FPGA first, so DONE high afterwards means it came from flash.
open_hw_manager
connect_hw_server -allow_non_jtag
set targets [get_hw_targets *]
if {[llength $targets] == 0} { puts "BOOT ERROR: no JTAG target"; exit 1 }
open_hw_target [lindex $targets 0]
set dev [lindex [get_hw_devices xc7a100t*] 0]
current_hw_device $dev
set t0 [clock milliseconds]
if {[catch {boot_hw_device $dev} err]} { puts "BOOT ERROR: did not boot from flash (is JP1 on QSPI?)"; exit 1 }
refresh_hw_device $dev
puts "BOOT done_pin=[get_property REGISTER.CONFIG_STATUS.BIT14_DONE_PIN $dev] after [expr {[clock milliseconds] - $t0}] ms"
close_hw_target
disconnect_hw_server
close_hw_manager
puts "BOOT OK"
