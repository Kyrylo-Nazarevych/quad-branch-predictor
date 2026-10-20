# Write a bitstream into the Nexys 4 DDR's QSPI flash (Spansion S25FL128S, 16 MB) so the FPGA loads it at power-on.
#   vivado -mode batch -source flash.tcl -tclargs <file.bit> <out dir>
# The board boots from the flash only with the JP1 mode jumper on QSPI. Erase + program + verify takes a few minutes.
set bit [file normalize [lindex $argv 0]]   ;# forward slashes: write_cfgmem reads backslashes as escapes
set out [file normalize [lindex $argv 1]]
set mcs [file join $out [file rootname [file tail $bit]].mcs]
# write_cfgmem splits -loadbit at spaces, so the bitstream goes through a temporary path without any
set tmp [file normalize [file join $::env(TEMP) bp_flash]]
if {[string first " " $tmp] >= 0} { set tmp C:/bp_flash_tmp }
file mkdir $tmp
file copy -force $bit [file join $tmp fpga_top.bit]
write_cfgmem -force -format mcs -size 16 -interface SPIx4 -loadbit "up 0x00000000 [file join $tmp fpga_top.bit]" -file $mcs
puts "FLASH image $mcs"

open_hw_manager
connect_hw_server -allow_non_jtag
set targets [get_hw_targets *]
if {[llength $targets] == 0} { puts "FLASH ERROR: no JTAG target. Is the board plugged into J6 and switched on?"; exit 1 }
open_hw_target [lindex $targets 0]
set dev [lindex [get_hw_devices xc7a100t*] 0]
current_hw_device $dev
refresh_hw_device -update_hw_probes false $dev

set part [lindex [get_cfgmem_parts {s25fl128sxxxxxx0-spi-x1_x2_x4}] 0]
create_hw_cfgmem -hw_device $dev -mem_dev $part
set cfg [get_property PROGRAM.HW_CFGMEM $dev]
set_property PROGRAM.ADDRESS_RANGE {use_file} $cfg
set_property PROGRAM.FILES [list $mcs] $cfg
set_property PROGRAM.PRM_FILE {} $cfg
set_property PROGRAM.UNUSED_PIN_TERMINATION {pull-none} $cfg
set_property PROGRAM.BLANK_CHECK 0 $cfg
set_property PROGRAM.ERASE 1 $cfg
set_property PROGRAM.CFG_PROGRAM 1 $cfg
set_property PROGRAM.VERIFY 1 $cfg
set_property PROGRAM.CHECKSUM 0 $cfg

# Vivado loads a small flash-programming design into the FPGA first (indirect programming)
create_hw_bitstream -hw_device $dev [get_property PROGRAM.HW_CFGMEM_BITFILE $dev]
program_hw_devices $dev
refresh_hw_device $dev
program_hw_cfgmem -hw_cfgmem $cfg
puts "FLASH programmed and verified"

# Try booting from the flash now. It only works with the JP1 mode jumper on QSPI; with JP1 on JTAG Vivado waits
# 180 s and gives up, and the FPGA stays blank until the next JTAG program. The flash itself is fine either way.
if {[catch {boot_hw_device $dev} err]} {
    puts "FLASH boot from flash did not finish: put the JP1 mode jumper on QSPI, then power-cycle or press PROG"
} else {
    refresh_hw_device $dev
    puts "FLASH booted from flash: done_pin=[get_property REGISTER.CONFIG_STATUS.BIT14_DONE_PIN $dev]"
}
close_hw_target
disconnect_hw_server
close_hw_manager
puts "FLASH OK"
