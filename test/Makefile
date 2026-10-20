# cocotb test of the chip RTL: replays the 704 reference trace through the pins.
#   pip install cocotb pytest ; make
SIM ?= icarus
TOPLEVEL_LANG ?= verilog
RTL = $(PWD)/../rtl
VERILOG_SOURCES = $(RTL)/latch_memory.v $(RTL)/perceptron_704.v $(RTL)/predictors.v $(RTL)/project.v
COCOTB_TOPLEVEL = tt_um_kyrylo_nazarevych_bp
COCOTB_TEST_MODULES = test_tt_um_kyrylo_nazarevych_bp
include $(shell cocotb-config --makefiles)/Makefile.sim
