// UART-level testbench for the FPGA design: plays the host side of protocol v1 over the serial pins and prints
// the same "R," lines and SUMMARY as tb_team.v, so the two outputs can be diffed line by line.
//
//   default : bp_hil_core alone, 25 MHz clock, CPB clocks per UART bit (fast; used for every trace)
//   +define+HIL_TOP : the full fpga_top (100 MHz board clock, MMCM, reset synchroniser) at BAUD; needs unisims
//
// Sequence: a stray data byte (must be counted as one framing error), PING, RESET, the whole trace streamed with at
// most WINDOW packets outstanding (like the host), then STATUS (must report exactly 1 framing error).
`timescale 1ns/1ps
// tb_params.vh (written by fpga.ps1 into the run folder) defines HIL_CPB and HIL_BAUD.
`include "tb_params.vh"
module tb_hil;
  parameter integer CPB      = `HIL_CPB;    // clocks per bit in core mode
  parameter integer BAUD     = `HIL_BAUD;   // top mode
  parameter integer WINDOW   = 64;
  parameter real    SKEW_PCT = 0.0;         // host bit time error, percent (+ = host slower)

`ifdef HIL_TOP
  localparam real BIT_NS = 1.0e9 / BAUD;
  reg clk100 = 0;
  always #5 clk100 = ~clk100;
  reg resetn = 0;
  wire [15:0] led;
  reg  host_tx = 1'b1;                      // into the FPGA
  wire host_rx;                             // out of the FPGA
  reg  [3:0] sw = 4'b0000;
  wire [7:0] an;
  wire [6:0] seg;
  wire dp;
  wire [5:0] rgb;
`ifdef HIL_NETLIST
  // post-route timing netlist (+define+HIL_NETLIST): the baud rate is fixed by the build
  fpga_top dut (
`else
  fpga_top #(.BAUD(BAUD)) dut (
`endif
      .CLK100MHZ(clk100), .CPU_RESETN(resetn), .UART_TXD_IN(host_tx), .UART_RXD_OUT(host_rx), .LED(led), .SW(sw),
      .CA(seg[0]), .CB(seg[1]), .CC(seg[2]), .CD(seg[3]), .CE(seg[4]), .CF(seg[5]), .CG(seg[6]), .DP(dp), .AN(an),
      .LED16_R(rgb[5]), .LED16_G(rgb[4]), .LED16_B(rgb[3]), .LED17_R(rgb[2]), .LED17_G(rgb[1]), .LED17_B(rgb[0]));
`define UI dut.u_ui
  initial begin #200; resetn = 1; end
`else
  localparam real BIT_NS = CPB * 40.0;
  reg clk = 0;
  always #20 clk = ~clk;                    // 25 MHz
  reg rst = 1;
  reg  host_tx = 1'b1;
  wire host_rx;
  wire chip_ready, framing_seen, rx_strobe, tx_strobe, branch_done;
  wire [4:0] last_branch;
  bp_hil_core #(.CLKS_PER_BIT(CPB)) dut (.clk(clk), .rst(rst), .uart_rx_pin(host_tx), .uart_tx_pin(host_rx),
      .chip_ready(chip_ready), .last_branch(last_branch), .branch_done(branch_done), .framing_seen(framing_seen),
      .rx_strobe(rx_strobe), .tx_strobe(tx_strobe));
  // the board display logic, driven by the core as in fpga_top, so every trace also checks the on-board numbers
  reg  [3:0] sw = 4'b0000;
  wire [6:0] seg_n;
  wire [7:0] an_n;
  wire dp_n;
  wire [3:0] correct_led;
  wire [2:0] rgb16, rgb17;
  board_ui ui (.clk(clk), .rst(rst), .chip_ready(chip_ready), .branch_done(branch_done), .last_branch(last_branch),
      .sw(sw), .seg_n(seg_n), .dp_n(dp_n), .an_n(an_n), .correct_led(correct_led), .rgb16(rgb16), .rgb17(rgb17));
`define UI ui
  initial begin repeat (10) @(posedge clk); rst = 0; end
`endif
  localparam real TX_BIT_NS = BIT_NS * (1.0 + SKEW_PCT / 100.0);

  // trace_init.vh defines N_BRANCHES and fills trace[i] = {taken, pc[7:0]}
  reg [8:0] trace [0:65535];
  integer N_BRANCHES;
  initial begin
    `include "trace_init.vh"
  end

  // ---------------------------------------------------------------- host -> board
  task send_byte(input [7:0] b);
    integer k;
    begin
      host_tx = 1'b0; #(TX_BIT_NS);
      for (k = 0; k < 8; k = k + 1) begin host_tx = b[k]; #(TX_BIT_NS); end
      host_tx = 1'b1; #(TX_BIT_NS);
    end
  endtask
  task send_packet(input [2:0] seq, input [1:0] op, input [7:0] pc, input taken);
    begin
      send_byte({1'b1, seq, op, pc[7], taken});
      send_byte({1'b0, pc[6:0]});
    end
  endtask

  integer sent = 0, received = 0;           // packets
  integer i;
  initial begin
    #1;
    #(20_000);                              // MMCM lock and reset release (top mode); harmless in core mode
    send_byte(8'h15);                       // stray data byte: must be ignored and counted
    send_packet(3'd0, 2'd2, 8'h00, 1'b0); sent = sent + 1;   // PING
    send_packet(3'd1, 2'd1, 8'h00, 1'b0); sent = sent + 1;   // RESET
    for (i = 0; i < N_BRANCHES; i = i + 1) begin
      while (sent - received >= WINDOW) #(BIT_NS);
      send_packet(sent[2:0], 2'd0, trace[i][7:0], trace[i][8]); sent = sent + 1;
    end
    send_packet(sent[2:0], 2'd3, 8'h00, 1'b0); sent = sent + 1;  // STATUS
  end

  // ---------------------------------------------------------------- board -> host
  task recv_byte(output [7:0] b, output ok);
    integer k;
    begin
      @(negedge host_rx);
      #(BIT_NS * 1.5);
      for (k = 0; k < 8; k = k + 1) begin b[k] = host_rx; #(BIT_NS); end
      ok = host_rx;                         // stop bit, sampled mid-bit
    end
  endtask

  integer errors = 0, m704 = 0, mbim = 0, mgsh = 0, mtour = 0, total_cyc = 0, b;
  reg [7:0] h, d;
  reg ok1, ok2, taken;
  integer ui_want;
  reg [31:0] ui_bcd;
  initial begin
    $display("R,index,pc8,taken,perceptron,bimodal,gshare,tournament,cycles");
    while (received < N_BRANCHES + 3) begin
      recv_byte(h, ok1);
      recv_byte(d, ok2);
      if (!ok1 || !ok2 || !h[7] || d[7]) begin
        $display("ERROR: bad reply framing at packet %0d: %02x %02x", received, h, d); errors = errors + 1;
      end
      if (h[6:4] != received[2:0]) begin
        $display("ERROR: reply %0d has seq %0d, expected %0d", received, h[6:4], received[2:0]); errors = errors + 1;
      end
      if (received == 0) begin
        if (h[3:0] != 4'h1 || d != 8'h50) begin $display("ERROR: PING reply %02x %02x", h, d); errors = errors + 1; end
      end else if (received == 1) begin
        if (h[3:0] != 4'h0 || d != 8'h52) begin $display("ERROR: RESET reply %02x %02x", h, d); errors = errors + 1; end
      end else if (received == N_BRANCHES + 2) begin
        if (d != 8'd1) begin $display("ERROR: STATUS reports %0d framing errors, expected 1", d); errors = errors + 1; end
        else $display("STATUS framing_errors=1 (the stray byte)");
      end else begin
        b = received - 2;
        taken = trace[b][8];
        $display("R,%0d,%02x,%0d,%0d,%0d,%0d,%0d,%0d", b, trace[b][7:0], taken, h[0], h[1], h[2], h[3], d);
        m704 = m704 + (h[0] != taken); mbim = mbim + (h[1] != taken);
        mgsh = mgsh + (h[2] != taken); mtour = mtour + (h[3] != taken);
        total_cyc = total_cyc + d;
      end
      received = received + 1;
    end
`ifndef HIL_NETLIST
    // the board display must show the same numbers: accuracy x100 rounded, and the branch count in BCD
    for (b = 0; b < 4; b = b + 1) begin
      sw = {b[1:0], 2'b00};
      #(4000);
      ui_want = ((N_BRANCHES - (b == 0 ? m704 : b == 1 ? mbim : b == 2 ? mgsh : mtour)) * 10000 + N_BRANCHES / 2)
                / N_BRANCHES;
      if (`UI.acc !== ui_want[13:0] || !`UI.acc_valid) begin
        $display("ERROR: display accuracy for predictor %0d is %0d, expected %0d", b, `UI.acc, ui_want); errors = errors + 1;
      end
    end
    ui_bcd = 0;
    for (b = 0, ui_want = N_BRANCHES; b < 8; b = b + 1, ui_want = ui_want / 10) ui_bcd[4*b +: 4] = ui_want % 10;
    sw = 4'b0001;
    #(200);
    if (`UI.bcd_count !== ui_bcd) begin
      $display("ERROR: display branch count %h, expected %h", `UI.bcd_count, ui_bcd); errors = errors + 1;
    end else
      $display("DISPLAY accuracy x100 and branch count match (%h)", ui_bcd);
`endif
    $display("SUMMARY branches=%0d acc_perceptron=%.2f acc_bimodal=%.2f acc_gshare=%.2f acc_tournament=%.2f avg_cycles=%.1f",
      N_BRANCHES, 100.0*(N_BRANCHES-m704)/N_BRANCHES, 100.0*(N_BRANCHES-mbim)/N_BRANCHES,
      100.0*(N_BRANCHES-mgsh)/N_BRANCHES, 100.0*(N_BRANCHES-mtour)/N_BRANCHES, 1.0*total_cyc/N_BRANCHES);
    $display("HIL_RESULT: %s (%0d protocol errors)", errors == 0 ? "PASS" : "FAIL", errors);
    $finish;
  end

`ifdef HIL_NETLIST
  // gate-level runs are slow: show progress
  initial forever begin #(50_000); $display("PROGRESS t=%0d us replies=%0d led=%b", $time / 1000, received, led); end
`endif

  // watchdog: 40 UART bits per branch is about twice what the stream needs
  initial begin
    #1;
    #(BIT_NS * 40.0 * (N_BRANCHES + 10) + 200_000);
    $display("HIL_RESULT: FAIL (timeout after %0d of %0d replies)", received, N_BRANCHES + 3);
    $finish;
  end
endmodule
