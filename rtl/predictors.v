// Branch predictor building blocks for Tiny Tapeout-sized designs.
// All predictors: combinational prediction for the PC on `pc`, update on the
// clock edge when `valid` is high, using the real outcome `taken`.
// (Trace-driven "predict, then resolve immediately" protocol.)

// ---------------------------------------------------------------- bimodal
module bimodal #(parameter IDX = 6) (
    input  wire           clk, rst_n, valid, taken,
    input  wire [IDX-1:0] pc,
    output wire           pred,
    output wire           saturated
);
    reg [1:0] ctr [0:(1<<IDX)-1];
    wire [1:0] c = ctr[pc];
    assign pred   = c[1];
    assign saturated = c[1] == c[0];
    integer i;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) for (i = 0; i < (1<<IDX); i = i + 1) ctr[i] <= 2'b10;
        else if (valid) ctr[pc] <= taken ? (c == 2'b11 ? c : c + 2'b01)
                                         : (c == 2'b00 ? c : c - 2'b01);
endmodule

// ---------------------------------------------------------------- gshare
module gshare #(parameter IDX = 6, HIST = 6) (
    input  wire           clk, rst_n, valid, taken,
    input  wire [IDX-1:0] pc,
    input  wire [HIST-1:0] ghr,          // shared global history register
    output wire           pred,
    output wire           saturated
);
    // fold HIST bits of history down to IDX bits
    function [IDX-1:0] fold(input [HIST-1:0] h);
        integer k; begin fold = 0; for (k = 0; k < HIST; k = k + 1) fold[k % IDX] = fold[k % IDX] ^ h[k]; end
    endfunction
    wire [IDX-1:0] idx = pc ^ fold(ghr);
    reg [1:0] ctr [0:(1<<IDX)-1];
    wire [1:0] c = ctr[idx];
    assign pred   = c[1];
    assign saturated = c[1] == c[0];
    integer i;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) for (i = 0; i < (1<<IDX); i = i + 1) ctr[i] <= 2'b10;
        else if (valid) ctr[idx] <= taken ? (c == 2'b11 ? c : c + 2'b01)
                                          : (c == 2'b00 ? c : c - 2'b01);
endmodule

// ---------------------------------------------------------------- chooser (tournament meta-predictor)
module chooser #(parameter IDX = 6) (
    input  wire           clk, rst_n, valid, taken,
    input  wire [IDX-1:0] pc,
    input  wire           p_bim, p_gsh,
    output wire           use_gsh,
    output wire           pred
);
    reg [1:0] ctr [0:(1<<IDX)-1];
    wire [1:0] c = ctr[pc];
    assign use_gsh = c[1];
    assign pred    = use_gsh ? p_gsh : p_bim;
    wire gsh_right = (p_gsh == taken);
    integer i;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) for (i = 0; i < (1<<IDX); i = i + 1) ctr[i] <= 2'b01;
        else if (valid && p_bim != p_gsh)
            ctr[pc] <= gsh_right ? (c == 2'b11 ? c : c + 2'b01) : (c == 2'b00 ? c : c - 2'b01);
endmodule

// ---------------------------------------------------------------- perceptron (single-cycle, parallel)
module perceptron #(parameter NPB = 3, HIST = 8, W = 7, THETA = 29) (
    input  wire            clk, rst_n, valid, taken,
    input  wire [NPB-1:0]  pc,
    input  wire [HIST-1:0] ghr,
    output wire            pred,
    output wire            confident
);
    localparam N = HIST + 1;            // + bias weight
    localparam SW = W + 5;              // sum width (enough for N <= 32)
    reg signed [W-1:0] w [0:(1<<NPB)*N-1];
    // dot product: inputs are +1 (taken) / -1 (not taken), bias input is +1
    reg signed [SW-1:0] y;
    integer k;
    always @* begin
        y = {{(SW-W){w[pc*N][W-1]}}, w[pc*N]};
        for (k = 0; k < HIST; k = k + 1)
            y = ghr[k] ? y + {{(SW-W){w[pc*N+k+1][W-1]}}, w[pc*N+k+1]}
                       : y - {{(SW-W){w[pc*N+k+1][W-1]}}, w[pc*N+k+1]};
    end
    assign pred = ~y[SW-1];
    wire [SW-1:0] mag = y[SW-1] ? -y : y;
    assign confident = mag > THETA;
    wire train = valid && ((pred != taken) || !confident);
    localparam signed [W-1:0] WMAX = (1 << (W-1)) - 1;
    localparam signed [W-1:0] WMIN = -(1 << (W-1));
    integer i;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) for (i = 0; i < (1<<NPB)*N; i = i + 1) w[i] <= 0;
        else if (train) begin
            // bias
            if (taken) begin if (w[pc*N] != WMAX) w[pc*N] <= w[pc*N] + 1; end
            else       begin if (w[pc*N] != WMIN) w[pc*N] <= w[pc*N] - 1; end
            for (k = 0; k < HIST; k = k + 1)
                if (ghr[k] == taken) begin if (w[pc*N+k+1] != WMAX) w[pc*N+k+1] <= w[pc*N+k+1] + 1; end
                else                 begin if (w[pc*N+k+1] != WMIN) w[pc*N+k+1] <= w[pc*N+k+1] - 1; end
        end
endmodule
