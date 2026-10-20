// UART transmitter, 8N1. Takes a byte when start is high and busy is low.
`default_nettype none
module uart_tx #(
    parameter CLKS_PER_BIT = 25
) (
    input  wire       clk,
    input  wire       rst,
    input  wire [7:0] data,
    input  wire       start,
    output wire       busy,
    output reg        tx
);
    localparam CW = $clog2(CLKS_PER_BIT + 1);

    reg [9:0]    frame;                      // {stop, data, start}, shifted out LSB first
    reg [3:0]    left;                       // bits still to send
    reg [CW-1:0] cnt;
    assign busy = (left != 0) || (cnt != 0);  // cnt != 0 holds the stop bit for its full length

    always @(posedge clk) begin
        if (rst) begin
            tx   <= 1'b1;
            left <= 0;
            cnt  <= 0;
        end else if (!busy) begin
            tx <= 1'b1;
            if (start) begin
                frame <= {1'b1, data, 1'b0};
                left  <= 4'd10;
                cnt   <= 0;
            end
        end else if (cnt == 0) begin
            tx    <= frame[0];
            frame <= {1'b1, frame[9:1]};
            cnt   <= CLKS_PER_BIT - 1;
            left  <= left - 1'b1;
        end else
            cnt <= cnt - 1'b1;
    end
endmodule
