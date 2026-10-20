/* Pattern: an inner loop of 12 iterations. Its exit branch repeats 11 x taken, then not taken.
   Compare with pattern_loop4: there the exit is 4 branches back and gshare and the perceptron learn it.
   Here the last 7 (perceptron) or 8 (gshare) outcomes before the exit are all "taken", exactly as in the middle
   of the loop, so no predictor on the chip can see the exit coming: every one misses it, about 1 in 12. */
volatile int sink;

int main(void) {
    for (int i = 0; i < 500; i++)
        for (int j = 0; j < 12; j++)
            sink = i + j;
    return 0;
}
