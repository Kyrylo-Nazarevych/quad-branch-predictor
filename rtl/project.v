/*
 * Hackathon top level: the 704 perceptron (Tristan Robitaille, Apache-2.0) plus a bimodal,
 * a gshare and a tournament predictor, all fed by the same pins and the same branch stream.
 *
 * Protocol (unchanged from the 704 design, so its tests and host code still work):
 *   1. put PC[7:0] on ui_in and the real outcome on uio_in[1]
 *   2. raise uio_in[0] (new_data_avail) for one clock
 *   3. bimodal / gshare / tournament answer two clocks after the strobe rises (uo_out[4], [5], [6]),
 *      the perceptron answers when pred_ready (uo_out[0]) goes high, on uo_out[1]
 *   4. wait for training_done (uo_out[2]) before sending the next branch
 */
`default_nettype none
module tt_um_kyrylo_nazarevych_bp #(
    parameter BIDX  = 6,   // 2^6 = 64 entries per table, indexed by PC[7:2]
    parameter GHIST = 8    // global history bits used by gshare
) (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
    // ---------------------------------------------------------------- the original 704 core
    wire [7:0] p_uo, p_uio_out, p_uio_oe;
    bp_perceptron_704 perceptron (
        .ui_in(ui_in), .uo_out(p_uo), .uio_in(uio_in), .uio_out(p_uio_out), .uio_oe(p_uio_oe),
        .ena(ena), .clk(clk), .rst_n(rst_n));

    // ---------------------------------------------------------------- input registers
    // The table predictors read the pins through flip-flops, so no table path starts at an input pin
    // (Tiny Tapeout's input delay plus the pin fan-out made those the only paths that missed timing).
    // It costs one clock: the table predictions are valid two clocks after the strobe rises.
    reg [BIDX-1:0] idx;                                 // PC[7:2], same >>2 as the 704 core
    reg taken, new_data_q, new_data_qq;
    always @(posedge clk) begin
        idx         <= ui_in[BIDX+1:2];
        taken       <= uio_in[1];
        new_data_q  <= rst_n ? uio_in[0] : 1'b0;
        new_data_qq <= rst_n ? new_data_q : 1'b0;
    end
    wire step = new_data_q & ~new_data_qq;              // one clock per branch

    reg [GHIST-1:0] ghr;                                // newest outcome in bit 0
    always @(posedge clk or negedge rst_n)
        if (!rst_n) ghr <= 0; else if (step) ghr <= {ghr[GHIST-2:0], taken};

    wire p_bim, p_gsh, p_tour, s_bim, s_gsh, use_gsh;
    bimodal #(.IDX(BIDX)) u_bim (.clk(clk), .rst_n(rst_n), .valid(step), .taken(taken),
        .pc(idx), .pred(p_bim), .saturated(s_bim));
    gshare  #(.IDX(BIDX), .HIST(GHIST)) u_gsh (.clk(clk), .rst_n(rst_n), .valid(step), .taken(taken),
        .pc(idx), .ghr(ghr), .pred(p_gsh), .saturated(s_gsh));
    chooser #(.IDX(BIDX)) u_cho (.clk(clk), .rst_n(rst_n), .valid(step), .taken(taken),
        .pc(idx), .p_bim(p_bim), .p_gsh(p_gsh), .use_gsh(use_gsh), .pred(p_tour));

    // capture the predictions made BEFORE this branch trains the tables
    reg r_bim, r_gsh, r_tour;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) {r_bim, r_gsh, r_tour} <= 3'b0;
        else if (step) {r_bim, r_gsh, r_tour} <= {p_bim, p_gsh, p_tour};

    // ---------------------------------------------------------------- pins
    //  uo[0] pred_ready   uo[1] perceptron prediction   uo[2] training_done   uo[3] mem_reset_done
    //  uo[4] bimodal      uo[5] gshare                  uo[6] tournament      uo[7] perceptron state bit
    assign uo_out  = {p_uo[5], r_tour, r_gsh, r_bim, p_uo[3:0]};
    assign uio_out = p_uio_out;
    assign uio_oe  = p_uio_oe;
    wire _unused = &{s_bim, s_gsh, use_gsh, p_uo[7:6], p_uo[4], 1'b0};
endmodule
