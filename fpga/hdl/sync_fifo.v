// Single-clock FIFO, first-word fall-through (dout is valid whenever empty is low).
`default_nettype none
module sync_fifo #(
    parameter WIDTH = 8,
    parameter DEPTH = 512                  // power of two
) (
    input  wire             clk,
    input  wire             rst,
    input  wire [WIDTH-1:0] din,
    input  wire             wr,
    input  wire             rd,
    output wire [WIDTH-1:0] dout,
    output wire             empty,
    output wire             full
);
    localparam AW = $clog2(DEPTH);
    reg [WIDTH-1:0] mem [0:DEPTH-1];
    reg [AW:0] wp, rp;                     // one extra bit tells full from empty

    assign empty = (wp == rp);
    assign full  = (wp[AW] != rp[AW]) && (wp[AW-1:0] == rp[AW-1:0]);
    assign dout  = mem[rp[AW-1:0]];

    always @(posedge clk) begin
        if (wr && !full) mem[wp[AW-1:0]] <= din;
        if (rst) begin
            wp <= 0;
            rp <= 0;
        end else begin
            if (wr && !full) wp <= wp + 1'b1;
            if (rd && !empty) rp <= rp + 1'b1;
        end
    end
endmodule
