// Demo display on the Nexys 4 DDR: live accuracy on the 8-digit 7-segment display, per-predictor LEDs, RGB LEDs.
//
//   SW0 = 0  accuracy of one predictor, e.g. "PE  84.48" (rounded to 2 decimals like the host)
//            SW1 = 1 cycles through the four predictors every 2 s, otherwise SW[3:2] picks one:
//            00 PE perceptron, 01 bi bimodal, 10 GS gshare, 11 to tournament
//   SW0 = 1  branches replayed since the last chip reset, in decimal
//   LD5-LD8  last branch right (1) or wrong (0): perceptron, bimodal, gshare, tournament
//   LD16     the selected predictor on the last branch: green right, red wrong (dimmed)
//   LD17     green chip ready, red chip in reset
// The statistics clear whenever the chip is in reset (power-up, CPU RESET, or the host's RESET packet), so the
// display always counts the same run as the host.
`default_nettype none
module board_ui (
    input  wire        clk,                 // 25 MHz
    input  wire        rst,
    input  wire        chip_ready,
    input  wire        branch_done,         // one clock per branch, last_branch valid from then on
    input  wire [4:0]  last_branch,         // {taken, tour, gshare, bimodal, perceptron}
    input  wire [3:0]  sw,
    output reg  [6:0]  seg_n,               // {CG, CF, CE, CD, CC, CB, CA}, active low
    output reg         dp_n,
    output reg  [7:0]  an_n,
    output wire [3:0]  correct_led,         // perceptron, bimodal, gshare, tournament
    output wire [2:0]  rgb16,               // {R, G, B}
    output wire [2:0]  rgb17
);
    // ------------------------------------------------------------ counters
    reg [31:0] total;
    reg [31:0] correct [0:3];
    reg [31:0] bcd_count;                    // 8 BCD digits
    reg        any;                          // at least one branch since reset
    wire [3:0] ok = {4{last_branch[4]}} ~^ last_branch[3:0];   // prediction == outcome, bit 0 = perceptron
    integer k;

    function [31:0] bcd_inc(input [31:0] b);
        integer d;
        reg carry;
        begin
            bcd_inc = b;
            carry = 1'b1;
            for (d = 0; d < 8; d = d + 1)
                if (carry) begin
                    if (b[4*d +: 4] == 4'd9) bcd_inc[4*d +: 4] = 4'd0;
                    else begin bcd_inc[4*d +: 4] = b[4*d +: 4] + 4'd1; carry = 1'b0; end
                end
        end
    endfunction

    always @(posedge clk) begin
        if (rst || !chip_ready) begin
            total     <= 0;
            bcd_count <= 0;
            any       <= 1'b0;
            for (k = 0; k < 4; k = k + 1) correct[k] <= 0;
        end else if (branch_done) begin                  // last_branch is already this branch's result
            total     <= total + 1'b1;
            bcd_count <= bcd_inc(bcd_count);
            any       <= 1'b1;
            for (k = 0; k < 4; k = k + 1) correct[k] <= correct[k] + ok[k];
        end
    end

    // ------------------------------------------------------------ which predictor
    reg [25:0] rot_cnt;                      // 2 s at 25 MHz = 50,000,000 clocks
    reg [1:0]  rot;
    always @(posedge clk)
        if (rst) begin rot_cnt <= 0; rot <= 0; end
        else if (rot_cnt == 26'd49_999_999) begin rot_cnt <= 0; rot <= rot + 1'b1; end
        else rot_cnt <= rot_cnt + 1'b1;
    wire [1:0] sel = sw[1] ? rot : sw[3:2];

    // ------------------------------------------------------------ accuracy = round(correct * 10000 / total)
    // A restoring divider, one quotient bit per clock (48 clocks), recomputed continuously.
    reg  [47:0] num;
    reg  [31:0] den;
    reg  [32:0] rem;
    reg  [13:0] q;
    reg  [5:0]  bitn;
    reg         busy;
    reg  [13:0] acc;                         // 0..10000 = 0.00% .. 100.00%
    reg         acc_valid;
    wire [32:0] rem_sh = {rem[31:0], num[47]};
    always @(posedge clk) begin
        if (rst) begin
            busy <= 1'b0;
            acc_valid <= 1'b0;
        end else if (!busy) begin
            if (total == 0) acc_valid <= 1'b0;
            else begin
                num  <= correct[sel] * 48'd10000 + (total >> 1);
                den  <= total;
                rem  <= 0;
                q    <= 0;
                bitn <= 6'd47;
                busy <= 1'b1;
            end
        end else begin
            num <= {num[46:0], 1'b0};
            if (rem_sh >= {1'b0, den}) begin rem <= rem_sh - {1'b0, den}; q <= {q[12:0], 1'b1}; end
            else begin rem <= rem_sh; q <= {q[12:0], 1'b0}; end
            if (bitn == 0) begin
                busy <= 1'b0;
                acc  <= (rem_sh >= {1'b0, den}) ? {q[12:0], 1'b1} : {q[12:0], 1'b0};
                acc_valid <= any;
            end
            bitn <= bitn - 1'b1;
        end
    end

    // binary 0..16383 to 5 BCD digits (double dabble)
    function [19:0] to_bcd(input [13:0] v);
        integer i, d;
        reg [33:0] s;
        begin
            s = {20'd0, v};
            for (i = 0; i < 14; i = i + 1) begin
                for (d = 0; d < 5; d = d + 1)
                    if (s[14 + 4*d +: 4] >= 5) s[14 + 4*d +: 4] = s[14 + 4*d +: 4] + 4'd3;
                s = s << 1;
            end
            to_bcd = s[33:14];
        end
    endfunction
    wire [19:0] acc_bcd = to_bcd(acc);

    // ------------------------------------------------------------ what each digit shows
    localparam C_P = 5'd10, C_E = 5'd11, C_B = 5'd12, C_I = 5'd13, C_G = 5'd14, C_S = 5'd15, C_T = 5'd16,
               C_O = 5'd17, C_BLANK = 5'd18, C_DASH = 5'd19;
    reg [4:0] code [0:7];
    reg [7:0] dps;
    integer d;
    reg lead;
    always @(*) begin
        dps = 8'b0;
        for (d = 0; d < 8; d = d + 1) code[d] = C_BLANK;
        if (sw[0]) begin                                 // branch count, leading zeros blanked
            lead = 1'b1;
            for (d = 7; d >= 0; d = d - 1) begin
                if (bcd_count[4*d +: 4] != 0 || d == 0) lead = 1'b0;
                code[d] = lead ? C_BLANK : {1'b0, bcd_count[4*d +: 4]};
            end
        end else begin
            case (sel)
                2'd0: begin code[7] = C_P; code[6] = C_E; end
                2'd1: begin code[7] = C_B; code[6] = C_I; end
                2'd2: begin code[7] = C_G; code[6] = C_S; end
                default: begin code[7] = C_T; code[6] = C_O; end
            endcase
            dps[2] = 1'b1;
            if (acc_valid) begin
                code[4] = acc_bcd[19:16] == 0 ? C_BLANK : {1'b0, acc_bcd[19:16]};
                code[3] = (acc_bcd[19:16] == 0 && acc_bcd[15:12] == 0) ? C_BLANK : {1'b0, acc_bcd[15:12]};
                code[2] = {1'b0, acc_bcd[11:8]};
                code[1] = {1'b0, acc_bcd[7:4]};
                code[0] = {1'b0, acc_bcd[3:0]};
            end else begin
                code[3] = C_DASH; code[2] = C_DASH; code[1] = C_DASH; code[0] = C_DASH;
            end
        end
    end

    function [6:0] segs(input [4:0] c);      // {g, f, e, d, c, b, a}, active high
        case (c)
            5'd0: segs = 7'b0111111;  5'd1: segs = 7'b0000110;  5'd2: segs = 7'b1011011;  5'd3: segs = 7'b1001111;
            5'd4: segs = 7'b1100110;  5'd5: segs = 7'b1101101;  5'd6: segs = 7'b1111101;  5'd7: segs = 7'b0000111;
            5'd8: segs = 7'b1111111;  5'd9: segs = 7'b1101111;
            C_P: segs = 7'b1110011;   C_E: segs = 7'b1111001;   C_B: segs = 7'b1111100;   C_I: segs = 7'b0000100;
            C_G: segs = 7'b0111101;   C_S: segs = 7'b1101101;   C_T: segs = 7'b1111000;   C_O: segs = 7'b1011100;
            C_DASH: segs = 7'b1000000;
            default: segs = 7'b0000000;
        endcase
    endfunction

    // ------------------------------------------------------------ multiplexing, about 95 Hz per digit
    reg [17:0] scan;
    always @(posedge clk) begin
        scan  <= scan + 1'b1;
        an_n  <= ~(8'b1 << scan[17:15]);
        seg_n <= ~segs(code[scan[17:15]]);
        dp_n  <= ~dps[scan[17:15]];
    end

    // ------------------------------------------------------------ LEDs
    reg [3:0] last_ok;
    always @(posedge clk)
        if (rst || !chip_ready) last_ok <= 4'b0;
        else if (branch_done) last_ok <= ok;
    assign correct_led = last_ok;
    wire dim = (scan[3:0] == 4'd0);          // 1/16 duty: the RGB LEDs are very bright
    assign rgb16 = (any && dim) ? (last_ok[sel] ? 3'b010 : 3'b100) : 3'b000;
    assign rgb17 = dim ? (chip_ready ? 3'b010 : 3'b100) : 3'b000;
endmodule
