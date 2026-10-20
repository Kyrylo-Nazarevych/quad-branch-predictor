// UART receiver, 8N1. One clock domain; the rx pin is synchronised here.
// CLKS_PER_BIT = clock / baud (25 MHz / 1 Mbaud = 25).
// Bytes with a bad start or stop bit are dropped and counted on frame_err (one-clock pulse).
`default_nettype none
module uart_rx #(
    parameter CLKS_PER_BIT = 25
) (
    input  wire       clk,
    input  wire       rst,
    input  wire       rx,
    output reg  [7:0] data,
    output reg        valid,      // one clock per received byte
    output reg        frame_err   // one clock per dropped byte
);
    localparam CW = $clog2(CLKS_PER_BIT + 1);
    localparam IDLE = 2'd0, START = 2'd1, BITS = 2'd2, STOP = 2'd3;

    reg rx_m = 1'b1, rx_s = 1'b1;            // two-flop synchroniser, idle high
    always @(posedge clk) begin
        rx_m <= rx;
        rx_s <= rx_m;
    end

    reg [1:0]    state;
    reg [CW-1:0] cnt;
    reg [2:0]    bitn;
    reg [7:0]    shift;

    always @(posedge clk) begin
        valid     <= 1'b0;
        frame_err <= 1'b0;
        if (rst) begin
            state <= IDLE;
            cnt   <= 0;
            bitn  <= 0;
        end else begin
            case (state)
                IDLE: if (!rx_s) begin              // falling edge of the start bit
                    state <= START;
                    cnt   <= CLKS_PER_BIT / 2;      // go to the middle of the start bit
                end
                START: if (cnt == 0) begin
                    if (!rx_s) begin                // still low: a real start bit
                        state <= BITS;
                        cnt   <= CLKS_PER_BIT - 1;
                        bitn  <= 0;
                    end else
                        state <= IDLE;              // glitch
                end else
                    cnt <= cnt - 1'b1;
                BITS: if (cnt == 0) begin
                    shift <= {rx_s, shift[7:1]};    // LSB first
                    cnt   <= CLKS_PER_BIT - 1;
                    if (bitn == 3'd7) state <= STOP;
                    bitn  <= bitn + 1'b1;
                end else
                    cnt <= cnt - 1'b1;
                STOP: if (cnt == 0) begin
                    state <= IDLE;
                    if (rx_s) begin
                        data  <= shift;
                        valid <= 1'b1;
                    end else
                        frame_err <= 1'b1;
                end else
                    cnt <= cnt - 1'b1;
            endcase
        end
    end
endmodule
