import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from "recharts";
import { api } from "../../api/client";
import { PageHeader } from "../../components/PageHeader";
import { StatusBadge } from "../../components/StatusBadge";
import { palette } from "../../palette";
import type { Device, MonitoringReading } from "../../types";

export function DeviceDetailPage() {
  const { id } = useParams();
  const [device, setDevice] = useState<Device | null>(null);
  const [readings, setReadings] = useState<MonitoringReading[]>([]);

  useEffect(() => {
    if (!id) return;
    api.get<Device>(`/devices/${id}/`).then((res) => setDevice(res.data));
    api.get<{ results: MonitoringReading[] }>(`/monitoring-readings/?device=${id}&page_size=200`).then((res) =>
      setReadings([...res.data.results].reverse())
    );
  }, [id]);

  if (!device) return <p className="text-[var(--text-muted)]">Loading…</p>;

  const chartData = readings.map((r) => ({
    time: new Date(r.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    in: r.bandwidth_in_mbps,
    out: r.bandwidth_out_mbps,
    latency: r.latency_ms,
  }));

  return (
    <div>
      <Link to="/admin/devices" className="mb-4 inline-block text-sm text-[var(--series-1)] hover:underline">
        ← Back to devices
      </Link>
      <PageHeader
        title={device.name}
        subtitle={`${device.ip_address} · ${device.location} · ${device.vendor} ${device.model_name}`}
        actions={<StatusBadge status={device.status} />}
      />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4 shadow-sm">
          <h2 className="mb-3 text-sm font-semibold">Bandwidth (Mbps)</h2>
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={chartData} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
              <CartesianGrid stroke={palette.gridline} vertical={false} />
              <XAxis dataKey="time" tick={{ fontSize: 10, fill: palette.textMuted }} axisLine={{ stroke: palette.gridline }} tickLine={false} minTickGap={30} />
              <YAxis tick={{ fontSize: 11, fill: palette.textMuted }} axisLine={false} tickLine={false} width={36} />
              <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8 }} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Line type="monotone" dataKey="in" name="Download" stroke={palette.series1} strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="out" name="Upload" stroke={palette.series2} strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>

        <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4 shadow-sm">
          <h2 className="mb-3 text-sm font-semibold">Latency (ms)</h2>
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={chartData} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
              <CartesianGrid stroke={palette.gridline} vertical={false} />
              <XAxis dataKey="time" tick={{ fontSize: 10, fill: palette.textMuted }} axisLine={{ stroke: palette.gridline }} tickLine={false} minTickGap={30} />
              <YAxis tick={{ fontSize: 11, fill: palette.textMuted }} axisLine={false} tickLine={false} width={36} />
              <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8 }} />
              <Line type="monotone" dataKey="latency" name="Latency" stroke={palette.series3} strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="mt-4 rounded-lg border border-[var(--border-hairline)] bg-black/[0.02] p-4 text-xs text-[var(--text-muted)]">
        Monitoring data on this page is simulated for demo purposes (see <code>simulate_monitoring</code> management
        command). Wire up real SNMP polling against <code>{device.ip_address}</code> using the device's SNMP
        community string to replace this with live data.
      </div>
    </div>
  );
}
