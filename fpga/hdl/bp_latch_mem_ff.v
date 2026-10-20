// FPGA-only drop-in for bp_latch_mem (rtl_chip/latch_memory.v): same module name, ports and clock-by-clock
// behaviour, but the 64 bytes are clocked storage instead of level-sensitive latches.
// Build with this file INSTEAD of latch_memory.v (fpga.ps1 -Ram ff). The chip RTL itself is not changed.
//
// Equivalence with the latch version:
//   write: the latch of the selected byte is transparent in the first half of the cycle that follows the edge
//          where wr_en_valid rises (wr_en_valid && wr_en_ok). Here the byte is written at that same edge, so the
//          new value is visible from the same point in the cycle and every later read sees the same data.
//          wr_en_valid rises at an edge exactly when rst_n && wr_en_next, which is the write condition below.
//   read:  the latch version reads RAM[{partition, addr_read}] where both parts are registered copies of addr_in,
//          then a 4-way tri-state bus; here it is one registered address and a plain mux.
`default_nettype none
module bp_latch_mem #(
    parameter RAM_BYTES = 64
) (
    input  wire [7:0] ui_in,    // {wr_en, padding, addr}
    output wire [7:0] uo_out,   // read data
    input  wire [7:0] uio_in,   // write data
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  localparam addr_bits = $clog2(RAM_BYTES);
  assign uio_oe  = 8'b0;
  assign uio_out = 8'b0;

  wire [addr_bits-1:0] addr_in = ui_in[addr_bits-1:0];
  wire wr_en_in = ui_in[7];
  reg  wr_en_next;
  reg  wr_en_valid;
  reg  [addr_bits-1:0] addr_write;
  reg  [7:0] data_to_write;
  reg  [addr_bits-1:0] addr_read;

  // Same write-request logic as latch_memory.v.
  wire wr_en_in_valid = wr_en_in && !wr_en_next;
  always @(posedge clk) begin
    if (!rst_n) begin
      wr_en_next <= 0;
      wr_en_valid <= 0;
    end else begin
      wr_en_next <= wr_en_in_valid;
      wr_en_valid <= wr_en_next;
    end
    addr_read <= addr_in;
    if (wr_en_in_valid) begin
      addr_write <= addr_in;
      data_to_write <= uio_in;
    end
  end

  reg [7:0] RAM [0:RAM_BYTES-1];
  always @(posedge clk)
    if (rst_n && wr_en_next)
      RAM[addr_write] <= data_to_write;

  assign uo_out = RAM[addr_read];

  wire _unused = &{ui_in[6:addr_bits], ena, wr_en_valid, 1'b0};
endmodule
