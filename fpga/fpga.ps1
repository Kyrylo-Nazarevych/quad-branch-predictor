<#
FPGA flow for the branch-predictor chip on the Nexys 4 DDR. Everything runs Vivado 2024.2 in batch mode.

  .\fpga\fpga.ps1 sim     -Trace <trace dir> [-Ram latch|ff] [-Top] [-Cpb 5]   UART testbench on one trace
  .\fpga\fpga.ps1 simref  -Trace <trace dir> [-Ram latch|ff]                    tb_team.v (pin-level reference)
  .\fpga\fpga.ps1 simall  [-Ram latch|ff|both]                                  every trace: models + tb_team diff
  .\fpga\fpga.ps1 build   [-Ram latch|ff] [-Baud 1000000]                       synth, place, route, bitstream
  .\fpga\fpga.ps1 timesim [-Ram latch|ff] [-Trace <dir>] [-Branches 300] [-Funcsim]
                                  post-route netlist with SDF delays (slow), or with -Funcsim the post-synthesis netlist
  .\fpga\fpga.ps1 program [-Ram latch|ff]                                       load the bitstream over JTAG
  .\fpga\fpga.ps1 flash   [-Ram latch|ff]                                       write it to the QSPI flash (boots with JP1 on QSPI)
  .\fpga\fpga.ps1 boot                                                          reload the FPGA from the flash, as at power-on

Outputs go to fpga\build\ (not committed).
#>
param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet("sim", "simref", "simall", "build", "timesim", "program", "flash", "boot")]
    [string]$Cmd,
    [string]$Trace,
    [ValidateSet("latch", "ff", "both")]
    [string]$Ram = "latch",
    [switch]$Top,
    [switch]$Funcsim,
    [int]$Cpb = 5,
    [int]$Baud = 1000000,
    [int]$Branches = 300,
    [string]$Vivado = "C:\Xilinx\Vivado\2024.2"
)
$ErrorActionPreference = "Continue"   # Vivado writes to stderr; failures are caught through exit codes
$Fpga  = $PSScriptRoot
$Repo  = Split-Path $Fpga -Parent
$Build = Join-Path $Fpga "build"
$Bin   = Join-Path $Vivado "bin"
$Compare = Join-Path $Repo "verification\tools\compare_predictions.py"

# trace folders checked by simall: verification/ (with expected files) and the precomputed set
$TraceRoots = @((Join-Path $Repo "verification\traces"), (Join-Path $Repo "tools\precomputed"))

function Chip-Sources([string]$ram) {
    $s = @("project.v", "perceptron_704.v", "predictors.v") | ForEach-Object { Join-Path $Repo "rtl\$_" }
    if ($ram -eq "ff") { $s += Join-Path $Fpga "hdl\bp_latch_mem_ff.v" }
    else               { $s += Join-Path $Repo "rtl\latch_memory.v" }
    return $s
}
function Hil-Sources {
    return @("uart_rx.v", "uart_tx.v", "sync_fifo.v", "bp_hil_core.v", "board_ui.v") | ForEach-Object { Join-Path $Fpga "hdl\$_" }
}
function Invoke-Tool([string]$exe, [string[]]$toolArgs, [string]$log) {
    & (Join-Path $Bin $exe) @toolArgs *> $log
    if ($LASTEXITCODE -ne 0) { throw "$exe failed (exit $LASTEXITCODE), see $log" }
}
function R-Lines([string]$file) { return @(Get-Content $file | Where-Object { $_ -like "R,*" }) }

# Compile and run one testbench on one trace; returns the simulator output file.
function Run-Xsim([string]$tb, [string]$traceDir, [string]$ram, [bool]$top, [int]$cpb) {
    $traceDir = (Resolve-Path $traceDir).Path
    $name = Split-Path $traceDir -Leaf
    $mode = if ($tb -eq "tb_team") { "team" } elseif ($top) { "hiltop" } else { "hil" }
    $dir = Join-Path $Build "sim\$ram\$name\$mode"
    New-Item -ItemType Directory -Force $dir | Out-Null
    Push-Location $dir
    try {
        $src = Chip-Sources $ram
        $defs = @("-d", "SIM")
        if ($tb -eq "tb_team") {
            $src += Join-Path $Fpga "sim\tb_team.v"
        } else {
            $src += Hil-Sources
            if ($top) { $src += Join-Path $Fpga "hdl\fpga_top.v"; $src += Join-Path $Vivado "data\verilog\src\glbl.v"; $defs += @("-d", "HIL_TOP") }
            $src += Join-Path $Fpga "sim\tb_hil.v"
        }
        # the .bat wrappers split arguments at "=", so parameters travel in an include file
        Set-Content -Encoding ascii "tb_params.vh" @("``define HIL_CPB $cpb", "``define HIL_BAUD $Baud")
        Invoke-Tool "xvlog.bat" (@("--nolog") + $defs + @("-i", $traceDir, "-i", $dir) + $src) "compile.txt"
        $elab = @("--nolog", "-timescale", "1ns/1ps", "-s", "snap", "work.$tb")
        if ($tb -eq "tb_hil") {
            if ($top) { $elab += @("work.glbl", "-L", "unisims_ver") }
        }
        Invoke-Tool "xelab.bat" $elab "elab.txt"
        Invoke-Tool "xsim.bat" @("snap", "-R", "--nolog") "sim_out.txt"
        return (Join-Path $dir "sim_out.txt")
    } finally { Pop-Location }
}

function Write-Csv([string]$simOut) {
    $csv = Join-Path (Split-Path $simOut -Parent) "predictions.csv"
    R-Lines $simOut | ForEach-Object { $_.Substring(2) } | Set-Content -Encoding ascii $csv
    return $csv
}

switch ($Cmd) {
    "sim" {
        $ram = if ($Ram -eq "both") { "latch" } else { $Ram }
        $out = Run-Xsim "tb_hil" $Trace $ram $Top.IsPresent $Cpb
        Get-Content $out | Where-Object { $_ -match "^(SUMMARY|HIL_RESULT|STATUS|ERROR|DISPLAY)" }
        $csv = Write-Csv $out
        python -I $Compare $Trace $csv
        Write-Host "predictions: $csv"
    }
    "simref" {
        $ram = if ($Ram -eq "both") { "latch" } else { $Ram }
        $out = Run-Xsim "tb_team" $Trace $ram $false 0
        Get-Content $out | Where-Object { $_ -match "^(SUMMARY|VULKOS)" }
        Write-Host "output: $out"
    }
    "simall" {
        $rams = if ($Ram -eq "both") { @("latch", "ff") } else { @($Ram) }
        $traces = $TraceRoots | ForEach-Object { Get-ChildItem $_ -Directory } | Where-Object { Test-Path (Join-Path $_.FullName "trace_init.vh") }
        $rows = @()
        foreach ($ram in $rams) {
            foreach ($t in $traces) {
                $t0 = Get-Date
                $ref = Run-Xsim "tb_team" $t.FullName $ram $false 0
                $hil = Run-Xsim "tb_hil" $t.FullName $ram $false $Cpb
                $csv = Write-Csv $hil
                $cmp = python -I $Compare $t.FullName $csv
                $models = if ($LASTEXITCODE -eq 0) { "IDENTICAL" } else { "DIFFERENT" }
                $same = ((R-Lines $ref) -join "`n") -eq ((R-Lines $hil) -join "`n")
                $pass = (Select-String -Path $hil -Pattern "HIL_RESULT: PASS" -Quiet)
                $sum = (Get-Content $hil | Where-Object { $_ -like "SUMMARY*" }) -replace "SUMMARY ", ""
                $rows += [pscustomobject]@{
                    ram = $ram; trace = "$(Split-Path $t.Parent.FullName -Leaf)/$($t.Name)"
                    vs_models = $models; vs_tb_team = $(if ($same) { "same" } else { "DIFFERENT" })
                    protocol = $(if ($pass) { "PASS" } else { "FAIL" }); secs = [int]((Get-Date) - $t0).TotalSeconds; summary = $sum
                }
                $rows[-1] | Format-List | Out-String | Write-Host
                if ($models -ne "IDENTICAL") { $cmp | Write-Host }
            }
        }
        $rows | Format-Table -AutoSize ram, trace, vs_models, vs_tb_team, protocol, secs | Out-String -Width 200
        $report = Join-Path $Build "simall_report.csv"
        $rows | Export-Csv -NoTypeInformation $report
        $bad = @($rows | Where-Object { $_.vs_models -ne "IDENTICAL" -or $_.vs_tb_team -ne "same" -or $_.protocol -ne "PASS" })
        if ($bad.Count -gt 0) { Write-Host "$($bad.Count) FAILING ROWS"; exit 1 } else { Write-Host "ALL $($rows.Count) RUNS PASS ($report)" }
    }
    "build" {
        $ram = if ($Ram -eq "both") { "latch" } else { $Ram }
        $dir = Join-Path $Build "impl_$ram"
        New-Item -ItemType Directory -Force $dir | Out-Null
        Push-Location $dir
        try {
            & (Join-Path $Bin "vivado.bat") -mode batch -nojournal -log build.log -source (Join-Path $Fpga "scripts\build.tcl") -tclargs $ram $Baud *> console.txt
            $code = $LASTEXITCODE
        } finally { Pop-Location }
        Get-Content (Join-Path $dir "build.log") | Where-Object { $_ -match "^(BUILD|ERROR|CRITICAL WARNING: \[(Timing|DRC|Place|Route))" }
        if ($code -ne 0) { throw "build failed, see $dir\build.log" }
    }
    "timesim" {
        $ram = if ($Ram -eq "both") { "latch" } else { $Ram }
        $dcp = Join-Path $Build "impl_$ram\post_route.dcp"
        if (-not (Test-Path $dcp)) { throw "no routed design at $dcp; run build first" }
        if (-not $Trace) { $Trace = Join-Path $Repo "verification\traces\ref704_committed_log" }
        $src = (Resolve-Path $Trace).Path
        $dir = Join-Path $Build $(if ($Funcsim) { "funcsim_$ram" } else { "timesim_$ram" })
        $tdir = Join-Path $dir "trace"
        New-Item -ItemType Directory -Force $tdir | Out-Null
        # first N branches of the trace, as both trace_init.vh and tb_input.hex
        $hex = @(Get-Content (Join-Path $src "tb_input.hex") | Select-Object -First $Branches)
        $n = $hex.Count
        $vh = @("N_BRANCHES = $n;") + @(Get-Content (Join-Path $src "trace_init.vh") | Where-Object { $_ -match '^trace\[(\d+)\]' -and [int]$matches[1] -lt $n })
        Set-Content -Encoding ascii (Join-Path $tdir "trace_init.vh") $vh
        Set-Content -Encoding ascii (Join-Path $tdir "tb_input.hex") $hex
        Push-Location $dir
        try {
            if ($Funcsim) {
                $netlist = Join-Path $Build "impl_$ram\post_synth_funcsim.v"
                $elabLib = @("-L", "unisims_ver")
            } else {
                & (Join-Path $Bin "vivado.bat") -mode batch -nojournal -log netlist.log -source (Join-Path $Fpga "scripts\timesim_netlist.tcl") -tclargs $dcp $dir *> netlist_console.txt
                if ($LASTEXITCODE -ne 0) { throw "netlist export failed, see $dir\netlist.log" }
                $netlist = Join-Path $dir "fpga_top_timesim.v"
                $elabLib = @("-L", "simprims_ver", "-maxdelay", "-transport_int_delays",
                             "-pulse_r", "0", "-pulse_int_r", "0", "-pulse_e", "0", "-pulse_int_e", "0")   # as Vivado's own timesim
            }
            Set-Content -Encoding ascii "tb_params.vh" @("``define HIL_CPB 25", "``define HIL_BAUD $Baud")
            $srcs = @($netlist, (Join-Path $Vivado "data\verilog\src\glbl.v"), (Join-Path $Fpga "sim\tb_hil.v"))
            Invoke-Tool "xvlog.bat" (@("--nolog", "-d", "HIL_TOP", "-d", "HIL_NETLIST", "-i", $tdir, "-i", $dir) + $srcs) "compile.txt"
            Invoke-Tool "xelab.bat" (@("--nolog", "-timescale", "1ns/1ps", "-s", "snap", "work.tb_hil", "work.glbl") + $elabLib) "elab.txt"
            $t0 = Get-Date
            Invoke-Tool "xsim.bat" @("snap", "-R", "--nolog") "sim_out.txt"
            Write-Host ("netlist simulation of $n branches took {0:N0} s" -f ((Get-Date) - $t0).TotalSeconds)
        } finally { Pop-Location }
        $out = Join-Path $dir "sim_out.txt"
        Get-Content $out | Where-Object { $_ -match "^(SUMMARY|HIL_RESULT|STATUS|ERROR|DISPLAY)" }
        Select-String -Path $out -Pattern "Timing violation|setup|hold" | Select-Object -First 10 | ForEach-Object Line
        $csv = Write-Csv $out
        python -I $Compare $tdir $csv
    }
    "program" {        $ram = if ($Ram -eq "both") { "latch" } else { $Ram }
        $bit = Join-Path $Build "impl_$ram\fpga_top_$ram.bit"
        if (-not (Test-Path $bit)) { throw "no bitstream at $bit; run build first" }
        $dir = Join-Path $Build "program"
        New-Item -ItemType Directory -Force $dir | Out-Null
        Push-Location $dir
        try {
            & (Join-Path $Bin "vivado.bat") -mode batch -nojournal -log program.log -source (Join-Path $Fpga "scripts\program.tcl") -tclargs $bit *> console.txt
            $code = $LASTEXITCODE
        } finally { Pop-Location }
        Get-Content (Join-Path $dir "program.log") | Where-Object { $_ -match "^(PROGRAM|ERROR)" }
        if ($code -ne 0) { throw "programming failed, see $dir\program.log" }
    }
    "flash" {
        $ram = if ($Ram -eq "both") { "latch" } else { $Ram }
        $bit = Join-Path $Build "impl_$ram\fpga_top_$ram.bit"
        if (-not (Test-Path $bit)) { throw "no bitstream at $bit; run build first" }
        $dir = Join-Path $Build "flash"
        New-Item -ItemType Directory -Force $dir | Out-Null
        Push-Location $dir
        try {
            & (Join-Path $Bin "vivado.bat") -mode batch -nojournal -log flash.log -source (Join-Path $Fpga "scripts\flash.tcl") -tclargs $bit $dir *> console.txt
            $code = $LASTEXITCODE
        } finally { Pop-Location }
        Get-Content (Join-Path $dir "flash.log") | Where-Object { $_ -match "^(FLASH|ERROR|CRITICAL WARNING)" }
        if ($code -ne 0) { throw "flash programming failed, see $dir\flash.log" }
    }
    "boot" {
        $dir = Join-Path $Build "flash"
        New-Item -ItemType Directory -Force $dir | Out-Null
        Push-Location $dir
        try {
            & (Join-Path $Bin "vivado.bat") -mode batch -nojournal -log boot.log -source (Join-Path $Fpga "scripts\boot_flash.tcl") *> boot_console.txt
            $code = $LASTEXITCODE
        } finally { Pop-Location }
        Get-Content (Join-Path $dir "boot.log") | Where-Object { $_ -match "^BOOT" }
        if ($code -ne 0) { throw "boot from flash failed, see $dir\boot.log" }
    }
}