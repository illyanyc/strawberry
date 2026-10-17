# Runbook: clock skew

Alert: `ClockSkew` (critical): `tb_clock_skew_seconds` > 0.25 s for 5 minutes (OP-020). While skewed, trading services refuse to open new risk (section 07).

Containers share the Colima VM's clock, which can drift after the Mac sleeps or the VM is paused.
1. Check the host: `sntp -t 2 time.apple.com` on macOS.
2. Check the VM: `colima ssh -- chronyc tracking` (or `date -u` vs a reference).
3. Fix the VM: `colima ssh -- sudo chronyc makestep`; if chrony is missing, `colima restart`.
4. Prevent: `pmset sleep 0` (installed by `infra/macos/install.sh`).
5. The alert clears after the next measurement (1-minute interval).
