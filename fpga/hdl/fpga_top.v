// Nexys 4 DDR top level: 100 MHz board clock -> MMCM -> 25 MHz for everything, reset synchroniser,
// the hardware-in-the-loop core (UART + chip handshake), and the demo display (board_ui.v).
//
// LEDs: LD0-3 last predictions (perceptron, bimodal, gshare, tournament), LD4 last outcome,
//       LD5-8 last branch right/wrong per predictor, LD12 UART traffic, LD13 framing error seen (sticky until
//       reset), LD14 chip ready, LD15 heartbeat. 7-segment display, switches and RGB LEDs: see board_ui.v.
`default_nettype none
module fpga_top #(
    parameter CLK_HZ = 25_000_000,
    parameter BAUD   = 1_000_000
) (
    input  wire        CLK100MHZ,
    input  wire        CPU_RESETN,      // red CPU RESET button, low when pressed
    input  wire        UART_TXD_IN,     // PC -> FPGA
    output wire        UART_RXD_OUT,    // FPGA -> PC
    output wire [15:0] LED,
    input  wire [3:0]  SW,              // display mode and predictor (board_ui.v)
    output wire        CA, CB, CC, CD, CE, CF, CG, DP,
    output wire [7:0]  AN,
    output wire        LED16_R, LED16_G, LED16_B,
    output wire        LED17_R, LED17_G, LED17_B
);
    // ------------------------------------------------------------ clock: 100 MHz -> 25 MHz
    wire clk_fb, clk25_mmcm, clk, locked;
    MMCME2_BASE #(
        .CLKIN1_PERIOD(10.0),
        .DIVCLK_DIVIDE(1),
        .CLKFBOUT_MULT_F(10.0),          // VCO 1000 MHz
        .CLKOUT0_DIVIDE_F(40.0)          // 25 MHz
    ) u_mmcm (
        .CLKIN1(CLK100MHZ), .CLKFBIN(clk_fb), .CLKFBOUT(clk_fb), .CLKOUT0(clk25_mmcm),
        .CLKFBOUTB(), .CLKOUT0B(), .CLKOUT1(), .CLKOUT1B(), .CLKOUT2(), .CLKOUT2B(), .CLKOUT3(), .CLKOUT3B(),
        .CLKOUT4(), .CLKOUT5(), .CLKOUT6(),
        .LOCKED(locked), .PWRDWN(1'b0), .RST(1'b0));
    BUFG u_bufg (.I(clk25_mmcm), .O(clk));

    // ------------------------------------------------------------ reset: button or MMCM not locked
    (* ASYNC_REG = "TRUE" *) reg [2:0] rst_sync = 3'b111;
    always @(posedge clk or negedge locked)
        if (!locked) rst_sync <= 3'b111;
        else         rst_sync <= {rst_sync[1:0], ~CPU_RESETN};
    wire rst = rst_sync[2];

    // ------------------------------------------------------------ core
    wire       chip_ready, framing_seen, rx_strobe, tx_strobe, branch_done;
    wire [4:0] last_branch;
    bp_hil_core #(.CLKS_PER_BIT(CLK_HZ / BAUD)) u_core (
        .clk(clk), .rst(rst), .uart_rx_pin(UART_TXD_IN), .uart_tx_pin(UART_RXD_OUT),
        .chip_ready(chip_ready), .last_branch(last_branch), .branch_done(branch_done), .framing_seen(framing_seen),
        .rx_strobe(rx_strobe), .tx_strobe(tx_strobe));

    // ------------------------------------------------------------ 7-segment display and RGB LEDs
    (* ASYNC_REG = "TRUE" *) reg [3:0] sw_m, sw_s;
    always @(posedge clk) begin sw_m <= SW; sw_s <= sw_m; end
    wire [3:0] correct_led;
    wire [6:0] seg_n;
    board_ui u_ui (
        .clk(clk), .rst(rst), .chip_ready(chip_ready), .branch_done(branch_done), .last_branch(last_branch),
        .sw(sw_s), .seg_n(seg_n), .dp_n(DP), .an_n(AN), .correct_led(correct_led),
        .rgb16({LED16_R, LED16_G, LED16_B}), .rgb17({LED17_R, LED17_G, LED17_B}));
    assign {CG, CF, CE, CD, CC, CB, CA} = seg_n;

    // ------------------------------------------------------------ status LEDs
    reg [24:0] heartbeat = 0;
    always @(posedge clk) heartbeat <= heartbeat + 1'b1;     // bit 24 toggles about every 0.67 s

    reg [20:0] activity = 0;                                 // stretch UART traffic to about 84 ms
    always @(posedge clk)
        if (rx_strobe || tx_strobe) activity <= {21{1'b1}};
        else if (activity != 0)     activity <= activity - 1'b1;

    assign LED[3:0]   = last_branch[3:0];
    assign LED[4]     = last_branch[4];
    assign LED[8:5]   = correct_led;
    assign LED[11:9]  = 3'b0;
    assign LED[12]    = (activity != 0);
    assign LED[13]    = framing_seen;
    assign LED[14]    = chip_ready;
    assign LED[15]    = heartbeat[24];
endmodule
