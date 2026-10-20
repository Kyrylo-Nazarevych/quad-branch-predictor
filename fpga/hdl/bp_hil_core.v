// Hardware-in-the-loop core: UART packets in, the chip's pin handshake, UART replies out. One clock domain.
//
// Protocol v1, 8N1. Every packet is 2 bytes, every reply is 2 bytes, one reply per packet, in order.
// A header byte has bit 7 = 1, a data byte has bit 7 = 0, so the receiver can always resynchronise.
//
//   host -> board   H = {1, seq[2:0], op[1:0], pc[7], taken}     D = {0, pc[6:0]}
//                   op 0 BRANCH: replay one branch (pc8, taken)
//                   op 1 RESET:  reset the chip, wait for its memory clear (pc, taken ignored)
//                   op 2 PING:   version check
//                   op 3 STATUS: framing-error counter
//   board -> host   BRANCH  H = {1, seq, tour, gshare, bimodal, perceptron}   D = {0, cycles[6:0]}
//                   RESET   H = {1, seq, 4'h0}                                D = {0, 7'h52} ("R"), sent when ready
//                   PING    H = {1, seq, VERSION}                             D = {0, 7'h50} ("P")
//                   STATUS  H = {1, seq, 4'h0}                                D = {0, framing errors, saturating}
//
// cycles = clocks from the chip sampling the strobe to training_done, the same count as tb_team.v (11 to 45).
// The chip handshake mirrors tb_team.v: PC and outcome are set together with a one-clock strobe and held until
// training_done; the predictions are read when pred_ready rises.
`default_nettype none
module bp_hil_core #(
    parameter CLKS_PER_BIT = 25,       // 25 MHz / 1 Mbaud
    parameter RST_CLOCKS   = 16,       // clocks rst_n is held low
    parameter VERSION      = 4'h1
) (
    input  wire       clk,
    input  wire       rst,             // synchronous, active high: resets everything
    input  wire       uart_rx_pin,
    output wire       uart_tx_pin,
    // status for LEDs
    output wire       chip_ready,
    output reg  [4:0] last_branch,     // {taken, tour, gshare, bimodal, perceptron}
    output reg        branch_done,     // one clock per finished branch; last_branch holds its result
    output reg        framing_seen,    // sticky
    output wire       rx_strobe,
    output wire       tx_strobe
);
    // ------------------------------------------------------------ UART and FIFOs
    wire [7:0] rx_byte;
    wire       rx_valid, rx_ferr;
    uart_rx #(.CLKS_PER_BIT(CLKS_PER_BIT)) u_rx (.clk(clk), .rst(rst), .rx(uart_rx_pin),
        .data(rx_byte), .valid(rx_valid), .frame_err(rx_ferr));

    wire [7:0] rxf_dout;
    wire       rxf_empty, rxf_full;
    reg        rxf_rd;
    sync_fifo #(.WIDTH(8), .DEPTH(512)) u_rxf (.clk(clk), .rst(rst), .din(rx_byte), .wr(rx_valid),
        .rd(rxf_rd), .dout(rxf_dout), .empty(rxf_empty), .full(rxf_full));

    reg  [7:0] txf_din;
    reg        txf_wr;
    wire [7:0] txf_dout;
    wire       txf_empty, txf_full;
    wire       tx_busy;
    wire       tx_start = !txf_empty && !tx_busy;
    sync_fifo #(.WIDTH(8), .DEPTH(512)) u_txf (.clk(clk), .rst(rst), .din(txf_din), .wr(txf_wr),
        .rd(tx_start), .dout(txf_dout), .empty(txf_empty), .full(txf_full));
    uart_tx #(.CLKS_PER_BIT(CLKS_PER_BIT)) u_tx (.clk(clk), .rst(rst), .data(txf_dout), .start(tx_start),
        .busy(tx_busy), .tx(uart_tx_pin));

    assign rx_strobe = rx_valid;
    assign tx_strobe = tx_start;

    // ------------------------------------------------------------ packet parser
    localparam OP_BRANCH = 2'd0, OP_RESET = 2'd1, OP_PING = 2'd2, OP_STATUS = 2'd3;
    reg       have_hdr;                 // header byte received, waiting for its data byte
    reg [7:0] hdr;
    reg       pkt_valid;                // one complete packet waiting for the FSM
    reg [2:0] pkt_seq;
    reg [1:0] pkt_op;
    reg [7:0] pkt_pc;
    reg       pkt_taken;
    reg       pkt_take;                 // FSM consumed the packet this clock
    reg [6:0] ferr_count;               // framing problems: UART frame errors and out-of-place bytes

    always @(*) rxf_rd = !rxf_empty && !pkt_valid;

    always @(posedge clk) begin
        if (rst) begin
            have_hdr     <= 1'b0;
            pkt_valid    <= 1'b0;
            ferr_count   <= 0;
            framing_seen <= 1'b0;
        end else begin
            if (pkt_take) pkt_valid <= 1'b0;
            if (rxf_rd) begin
                if (rxf_dout[7]) begin                      // header
                    if (have_hdr) begin                     // previous header lost its data byte
                        if (ferr_count != 7'h7F) ferr_count <= ferr_count + 1'b1;
                        framing_seen <= 1'b1;
                    end
                    hdr      <= rxf_dout;
                    have_hdr <= 1'b1;
                end else if (have_hdr) begin                // data byte completes the packet
                    pkt_valid <= 1'b1;
                    pkt_seq   <= hdr[6:4];
                    pkt_op    <= hdr[3:2];
                    pkt_pc    <= {hdr[1], rxf_dout[6:0]};
                    pkt_taken <= hdr[0];
                    have_hdr  <= 1'b0;
                end else begin                              // data byte without a header: drop it
                    if (ferr_count != 7'h7F) ferr_count <= ferr_count + 1'b1;
                    framing_seen <= 1'b1;
                end
            end
            if (rx_ferr || (rx_valid && rxf_full)) begin   // bad UART frame, or a byte lost to a full FIFO
                if (ferr_count != 7'h7F) ferr_count <= ferr_count + 1'b1;
                framing_seen <= 1'b1;
            end
        end
    end

    // ------------------------------------------------------------ the chip
    reg  [7:0] chip_ui;
    reg        chip_strobe, chip_taken, chip_rst_n;
    wire [7:0] chip_uo, chip_uio_out, chip_uio_oe;
    tt_um_kyrylo_nazarevych_bp chip (
        .ui_in(chip_ui), .uo_out(chip_uo),
        .uio_in({6'b0, chip_taken, chip_strobe}), .uio_out(chip_uio_out), .uio_oe(chip_uio_oe),
        .ena(1'b1), .clk(clk), .rst_n(chip_rst_n));
    wire pred_ready     = chip_uo[0];
    wire training_done  = chip_uo[2];
    wire mem_reset_done = chip_uo[3];
    wire _unused = &{chip_uo[7], chip_uio_out, chip_uio_oe, 1'b0};

    // ------------------------------------------------------------ handshake FSM
    localparam S_RST_HOLD = 3'd0, S_RST_WAIT = 3'd1, S_IDLE = 3'd2, S_STROBE = 3'd3,
               S_WAIT = 3'd4, S_REPLY_H = 3'd5, S_REPLY_D = 3'd6;
    reg [2:0] state;
    reg [4:0] rst_cnt;
    reg       reply_reset;              // the reset came from a RESET packet: acknowledge it
    reg [2:0] cur_seq;
    reg [6:0] cyc;                      // clocks since the chip sampled the strobe
    reg       got_pred;
    reg [3:0] preds;                    // {tour, gshare, bimodal, perceptron}
    reg [7:0] reply_h;
    reg [6:0] reply_d;

    assign chip_ready = (state != S_RST_HOLD) && (state != S_RST_WAIT);

    always @(*) begin
        txf_wr  = 1'b0;
        txf_din = 8'h00;
        if (state == S_REPLY_H && !txf_full) begin txf_wr = 1'b1; txf_din = reply_h; end
        if (state == S_REPLY_D && !txf_full) begin txf_wr = 1'b1; txf_din = {1'b0, reply_d}; end
    end

    always @(*) pkt_take = (state == S_IDLE) && pkt_valid;

    always @(posedge clk) begin
        if (rst) begin
            state       <= S_RST_HOLD;
            rst_cnt     <= 0;
            reply_reset <= 1'b0;
            chip_rst_n  <= 1'b0;
            chip_strobe <= 1'b0;
            chip_taken  <= 1'b0;
            chip_ui     <= 8'h00;
            last_branch <= 5'b0;
            branch_done <= 1'b0;
        end else begin
            branch_done <= 1'b0;
            case (state)
                S_RST_HOLD: begin
                    chip_rst_n  <= 1'b0;
                    chip_strobe <= 1'b0;
                    rst_cnt     <= rst_cnt + 1'b1;
                    if (rst_cnt == RST_CLOCKS - 1) begin
                        chip_rst_n <= 1'b1;
                        state      <= S_RST_WAIT;
                    end
                end
                S_RST_WAIT: if (mem_reset_done) begin       // one-clock pulse, registered in the chip
                    if (reply_reset) begin
                        reply_h <= {1'b1, cur_seq, 4'h0};
                        reply_d <= 7'h52;
                        state   <= S_REPLY_H;
                    end else
                        state <= S_IDLE;
                    reply_reset <= 1'b0;
                end
                S_IDLE: if (pkt_valid) begin
                    cur_seq <= pkt_seq;
                    case (pkt_op)
                        OP_BRANCH: begin
                            chip_ui     <= pkt_pc;
                            chip_taken  <= pkt_taken;
                            chip_strobe <= 1'b1;
                            state       <= S_STROBE;
                        end
                        OP_RESET: begin
                            reply_reset <= 1'b1;
                            rst_cnt     <= 0;
                            state       <= S_RST_HOLD;
                        end
                        OP_PING: begin
                            reply_h <= {1'b1, pkt_seq, VERSION};
                            reply_d <= 7'h50;
                            state   <= S_REPLY_H;
                        end
                        default: begin                       // OP_STATUS
                            reply_h <= {1'b1, pkt_seq, 4'h0};
                            reply_d <= ferr_count;
                            state   <= S_REPLY_H;
                        end
                    endcase
                end
                S_STROBE: begin                              // the chip samples the strobe at this edge
                    chip_strobe <= 1'b0;
                    cyc         <= 7'd1;
                    got_pred    <= 1'b0;
                    state       <= S_WAIT;
                end
                S_WAIT: begin                                // chip outputs here are the values after edge `cyc`
                    if (pred_ready && !got_pred) begin
                        got_pred <= 1'b1;
                        preds    <= {chip_uo[6], chip_uo[5], chip_uo[4], chip_uo[1]};
                    end
                    if (training_done) begin
                        // pred_ready is high no later than training_done, so take the predictions now if needed
                        reply_h <= {1'b1, cur_seq, (got_pred ? preds : {chip_uo[6], chip_uo[5], chip_uo[4], chip_uo[1]})};
                        reply_d <= cyc;
                        last_branch <= {chip_taken, (got_pred ? preds : {chip_uo[6], chip_uo[5], chip_uo[4], chip_uo[1]})};
                        branch_done <= 1'b1;
                        state   <= S_REPLY_H;
                    end else if (cyc != 7'h7F)
                        cyc <= cyc + 1'b1;
                end
                S_REPLY_H: if (!txf_full) state <= S_REPLY_D;
                S_REPLY_D: if (!txf_full) state <= S_IDLE;
                default: state <= S_RST_HOLD;
            endcase
        end
    end
endmodule
