// Trace-driven testbench for tt_um_bp_team. Works in any Verilog simulator:
//   the trace is pulled in at compile time from trace_init.vh (written by bptrace --emit-vh),
//   results are printed to the console, one CSV line per branch starting with "R,".
// Vulkos: run with Icarus. latch_memory.v picks behavioural gates by itself when not synthesising.
// Icarus:  iverilog -g2012 -DSIM -o tbteam tb_team.v project.v perceptron_704.v latch_memory.v predictors.v
//          vvp -n tbteam > sim_out.txt ;  grep '^R,' sim_out.txt > predictions.csv
`timescale 1ns/1ps
module tb_team;
  reg clk = 0, rst_n = 0;
  reg  [7:0] ui_in = 0, uio_in = 0;
  wire [7:0] uo_out, uio_out, uio_oe;
  tt_um_kyrylo_nazarevych_bp dut (.ui_in(ui_in), .uo_out(uo_out), .uio_in(uio_in), .uio_out(uio_out),
                     .uio_oe(uio_oe), .ena(1'b1), .clk(clk), .rst_n(rst_n));
  always #50 clk = ~clk;                       // 10 MHz

  // trace_init.vh defines N_BRANCHES and fills trace[i] = {taken, pc[7:0]}
  reg [8:0] trace [0:65535];
  integer N_BRANCHES;
  initial begin
    `include "trace_init.vh"
  end

  // watchdog: 100 clocks per branch is far more than the worst case (45), plus the memory reset
  initial begin #1; #(N_BRANCHES * 10_000 + 1_000_000); $display("VULKOS_RESULT: FAIL (timeout)"); $finish; end

  integer i, cyc, m704 = 0, mbim = 0, mgsh = 0, mtour = 0, total_cyc = 0;
  reg taken;
  reg [3:0] preds;                             // {perceptron, bimodal, gshare, tournament}
  initial begin
    #1;
    $display("R,index,pc8,taken,perceptron,bimodal,gshare,tournament,cycles");
    repeat (5) @(posedge clk); rst_n = 1;
    wait (uo_out[3]);                          // mem_reset_done
    for (i = 0; i < N_BRANCHES; i = i + 1) begin
      taken = trace[i][8];
      @(negedge clk); ui_in = trace[i][7:0]; uio_in = {6'b0, taken, 1'b1};
      @(negedge clk); uio_in[0] = 1'b0;        // bimodal/gshare/tournament outputs are valid now
      cyc = 1;
      while (!uo_out[0]) begin @(negedge clk); cyc = cyc + 1; end   // perceptron pred_ready
      preds = {uo_out[1], uo_out[4], uo_out[5], uo_out[6]};
      m704 = m704 + (uo_out[1] != taken); mbim = mbim + (uo_out[4] != taken);
      mgsh = mgsh + (uo_out[5] != taken); mtour = mtour + (uo_out[6] != taken);
      while (!uo_out[2]) begin @(negedge clk); cyc = cyc + 1; end   // training_done
      total_cyc = total_cyc + cyc;
      // cycles = clocks per branch, from the strobe to training_done (what avg_cycles averages)
      $display("R,%0d,%02x,%0d,%0d,%0d,%0d,%0d,%0d", i, trace[i][7:0], taken,
               preds[3], preds[2], preds[1], preds[0], cyc);
    end
    $display("SUMMARY branches=%0d acc_perceptron=%.2f acc_bimodal=%.2f acc_gshare=%.2f acc_tournament=%.2f avg_cycles=%.1f",
      N_BRANCHES, 100.0*(N_BRANCHES-m704)/N_BRANCHES, 100.0*(N_BRANCHES-mbim)/N_BRANCHES,
      100.0*(N_BRANCHES-mgsh)/N_BRANCHES, 100.0*(N_BRANCHES-mtour)/N_BRANCHES, 1.0*total_cyc/N_BRANCHES);
    // self-check against the reference numbers for the 704 reference trace (1,271 branches)
    if (N_BRANCHES == 1271) begin
      if (m704 == 229 && mbim == 421 && mgsh == 172 && mtour == 142)
        $display("VULKOS_RESULT: PASS");
      else
        $display("VULKOS_RESULT: FAIL (mispredictions %0d %0d %0d %0d, expected 229 421 172 142)", m704, mbim, mgsh, mtour);
    end
    $finish;
  end
endmodule
