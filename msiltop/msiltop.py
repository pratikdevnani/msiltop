import time
import click
import asyncio
import json
from collections import deque
from typing import Optional

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import ProgressBar, Static, Label
from textual_plotext import PlotextPlot
import plotext as plt
from datetime import datetime
from .utils import run_powermetrics_process, parse_powermetrics, get_soc_info, get_ram_metrics_dict


class MetricGauge(Static):
    """Custom gauge widget to display metrics with progress bar and text"""
    
    def __init__(self, title: str = "", max_value: int = 100, **kwargs):
        super().__init__(**kwargs)
        self.title = title
        self.max_value = max_value
        self._value = 0
        
    def compose(self) -> ComposeResult:
        yield Label(self.title, id="gauge-title")
        yield ProgressBar(total=self.max_value, show_percentage=True, id="gauge-progress")
    
    def update_value(self, value: int, title: Optional[str] = None):
        self._value = value
        if title:
            self.title = title
        self.query_one("#gauge-title", Label).update(self.title)
        self.query_one("#gauge-progress", ProgressBar).update(progress=value)


class PowerChart(PlotextPlot):
    """Custom chart widget for power consumption data"""
    
    def __init__(self, title: str = "", interval: int = 1, color: str = "cyan", max_points: int = 1200, **kwargs):
        super().__init__(**kwargs)
        self.title = title
        self.interval = interval
        self.plot_color = color
        # Store up to max_points data points
        self.data_points = deque(maxlen=max_points)
        self.timestamps = deque(maxlen=max_points)
        self.start_time = time.time()
        # Track min/max values seen across all time
        self.min_value_seen = None
        self.max_value_seen = None
        self._last_value = None
        self._last_range = None
        self._last_ticks = None
        self._last_labels = None
        
    def on_mount(self):
        self.plt.title(self.title)
        self.plt.xlabel("Time (minutes ago)")
        self.plt.ylabel("Power (%)")
        # Apply custom colors before setting auto_theme
        # Set auto_theme to False to prevent overriding custom colors
        self.auto_theme = False
        self.plt.plotsize(None, None)  # Auto-size
        # Set Y-axis decimal precision
        self.plt.yfrequency(0)  # This will auto-determine the frequency
    
    def add_data(self, value: float, render: bool = True):
        current_time = time.time()
        self.data_points.append(value)
        self.timestamps.append(current_time)
        if not render:
            return
        if self._last_value is not None and abs(value - self._last_value) < 0.01:
            return
        self._last_value = value
        
        # Update min/max values seen
        if self.min_value_seen is None or value < self.min_value_seen:
            self.min_value_seen = value
        if self.max_value_seen is None or value > self.max_value_seen:
            self.max_value_seen = value
        
        self.plt.clear_data()
        
        # Always set up axes, even with no data
        # Use tracked min/max values for scaling
        if self.min_value_seen is not None and self.max_value_seen is not None:
            # Ensure a minimum range to avoid flat lines
            range_size = self.max_value_seen - self.min_value_seen
            if range_size < 0.5:  # Minimum 0.5% range
                # Center the range around the midpoint
                midpoint = (self.min_value_seen + self.max_value_seen) / 2
                y_min = max(0, midpoint - 0.25)
                y_max = midpoint + 0.25
            else:
                # Add 10% padding on both sides
                padding = range_size * 0.1
                y_min = max(0, self.min_value_seen - padding)  # Don't go below 0
                y_max = self.max_value_seen + padding
        else:
            # Default when no data
            y_min = 0
            y_max = 1.0
            
        # Create 5 evenly spaced y-ticks only when range changes
        current_range = (round(y_min, 2), round(y_max, 2))
        if current_range != self._last_range:
            y_ticks = []
            y_labels = []
            for i in range(5):
                val = y_min + (y_max - y_min) * i / 4
                y_ticks.append(val)
                y_labels.append(f"{val:.1f}")
            self._last_ticks = y_ticks
            self._last_labels = y_labels
            self._last_range = current_range
        self.plt.yticks(self._last_ticks, self._last_labels)
        # Set y-axis limits
        self.plt.ylim(y_min, y_max)
        
        # Set x-axis to show 0.0 to 0.6 minutes ago by default
        default_x_ticks = [0.0, -0.1, -0.2, -0.3, -0.4, -0.5, -0.6]
        default_x_labels = ["0.0", "0.1", "0.2", "0.3", "0.4", "0.5", "0.6"]
        
        if len(self.data_points) > 1:
            # Calculate time differences from now in minutes
            time_diffs = [(current_time - t) / 60 for t in self.timestamps]
            # Reverse so most recent is on the right
            time_diffs = [-td for td in time_diffs]
            
            # Use RGB color for plotting
            self.plt.plot(time_diffs, list(self.data_points), marker="braille", color=self.plot_color)
            
            # Set x-axis labels - show actual time values
            if len(time_diffs) >= 5:
                # Show 5 evenly spaced labels
                indices = [0, len(time_diffs)//4, len(time_diffs)//2, 3*len(time_diffs)//4, len(time_diffs)-1]
                ticks = [time_diffs[i] for i in indices]
                labels = [f"{abs(t):.1f}" for t in ticks]
                self.plt.xticks(ticks, labels)
            else:
                # For fewer points, show all
                labels = [f"{abs(t):.1f}" for t in time_diffs]
                self.plt.xticks(time_diffs, labels)
        else:
            # No data yet, show default x-axis
            self.plt.xticks(default_x_ticks, default_x_labels)
            self.plt.xlim(-0.6, 0)
        
        self.refresh()
    
    def update_title(self, title: str):
        self.title = title
        self.plt.title(title)
        self.refresh()


class UsageChart(PlotextPlot):
    """Custom chart widget for usage percentage data"""
    
    def __init__(self, title: str = "", ylabel: str = "Usage (%)", interval: int = 1, color: str = "cyan", max_points: int = 1200, **kwargs):
        super().__init__(**kwargs)
        self.title = title
        self.ylabel = ylabel
        self.interval = interval
        self.plot_color = color
        # Store up to max_points data points
        self.data_points = deque(maxlen=max_points)
        self.timestamps = deque(maxlen=max_points)
        self.start_time = time.time()
        # Track min/max values seen across all time
        self.min_value_seen = None
        self.max_value_seen = None
        self._last_value = None
        self._last_range = None
        self._last_ticks = None
        self._last_labels = None
        
    def on_mount(self):
        self.plt.title(self.title)
        self.plt.xlabel("Time (minutes ago)")
        self.plt.ylabel(self.ylabel)
        self.plt.ylim(0, 100)
        # Apply custom colors before setting auto_theme
        # Set auto_theme to False to prevent overriding custom colors
        self.auto_theme = False
        self.plt.plotsize(None, None)  # Auto-size
        # Set Y-axis decimal precision
        self.plt.yfrequency(0)  # This will auto-determine the frequency
    
    def add_data(self, value: float, render: bool = True):
        current_time = time.time()
        self.data_points.append(value)
        self.timestamps.append(current_time)
        if not render:
            return
        if self._last_value is not None and abs(value - self._last_value) < 0.1:
            return
        self._last_value = value
        
        # Update min/max values seen
        if self.min_value_seen is None or value < self.min_value_seen:
            self.min_value_seen = value
        if self.max_value_seen is None or value > self.max_value_seen:
            self.max_value_seen = value
        
        self.plt.clear_data()
        
        # Always set up axes, even with no data
        # For usage charts, we'll use dynamic scaling but ensure we can see the full 0-100 range if needed
        if self.min_value_seen is not None and self.max_value_seen is not None:
            # If values are spread across a wide range, show full 0-100
            if self.max_value_seen > 80 or (self.max_value_seen - self.min_value_seen) > 50:
                # Use traditional 0-100 scale
                y_min = 0
                y_max = 100
                y_ticks = [0, 25, 50, 75, 100]
            else:
                # Use dynamic scaling for better visibility of small variations
                range_size = self.max_value_seen - self.min_value_seen
                if range_size < 5:  # Minimum 5% range
                    # Center the range around the midpoint
                    midpoint = (self.min_value_seen + self.max_value_seen) / 2
                    y_min = max(0, midpoint - 2.5)
                    y_max = min(100, midpoint + 2.5)
                else:
                    # Add 10% padding on both sides
                    padding = range_size * 0.1
                    y_min = max(0, self.min_value_seen - padding)
                    y_max = min(100, self.max_value_seen + padding)
                
                # Create 5 evenly spaced y-ticks
                y_ticks = []
                for i in range(5):
                    val = y_min + (y_max - y_min) * i / 4
                    y_ticks.append(val)
        else:
            # Default when no data
            y_min = 0
            y_max = 100
            y_ticks = [0, 25, 50, 75, 100]
        
        current_range = (round(y_min, 2), round(y_max, 2))
        if current_range != self._last_range:
            y_labels = [f"{val:.1f}" for val in y_ticks]
            self._last_ticks = y_ticks
            self._last_labels = y_labels
            self._last_range = current_range
        self.plt.yticks(self._last_ticks, self._last_labels)
        self.plt.ylim(y_min, y_max)
        
        # Set x-axis to show 0.0 to 0.6 minutes ago by default
        default_x_ticks = [0.0, -0.1, -0.2, -0.3, -0.4, -0.5, -0.6]
        default_x_labels = ["0.0", "0.1", "0.2", "0.3", "0.4", "0.5", "0.6"]
        
        if len(self.data_points) > 1:
            # Calculate time differences from now in minutes
            time_diffs = [(current_time - t) / 60 for t in self.timestamps]
            # Reverse so most recent is on the right
            time_diffs = [-td for td in time_diffs]
            
            # Use RGB color for plotting
            self.plt.plot(time_diffs, list(self.data_points), marker="braille", color=self.plot_color)
            
            # Set x-axis labels - show actual time values
            if len(time_diffs) >= 5:
                # Show 5 evenly spaced labels
                indices = [0, len(time_diffs)//4, len(time_diffs)//2, 3*len(time_diffs)//4, len(time_diffs)-1]
                ticks = [time_diffs[i] for i in indices]
                labels = [f"{abs(t):.1f}" for t in ticks]
                self.plt.xticks(ticks, labels)
            else:
                # For fewer points, show all
                labels = [f"{abs(t):.1f}" for t in time_diffs]
                self.plt.xticks(time_diffs, labels)
        else:
            # No data yet, show default x-axis
            self.plt.xticks(default_x_ticks, default_x_labels)
            self.plt.xlim(-0.6, 0)
        
        self.refresh()
    
    def update_title(self, title: str):
        self.title = title
        self.plt.title(title)
        self.refresh()


class MultiLineChart(PlotextPlot):
    """Custom chart widget for displaying multiple data series on the same chart"""
    
    def __init__(self, title: str = "", ylabel: str = "Value", interval: int = 1, color: str = "cyan", max_points: int = 1200, **kwargs):
        super().__init__(**kwargs)
        self.title = title
        self.ylabel = ylabel
        self.interval = interval
        self.plot_color = color
        self.max_points = max_points
        # Store up to max_points data points for each series
        self.data_series = {}  # Will store {'series_name': {'data': deque, 'timestamps': deque}}
        self.start_time = time.time()
        self._last_values = {}
        
    def on_mount(self):
        self.plt.title(self.title)
        self.plt.xlabel("Time (minutes ago)")
        self.plt.ylabel(self.ylabel)
        # Apply custom colors before setting auto_theme
        # Set auto_theme to False to prevent overriding custom colors
        self.auto_theme = False
        self.plt.plotsize(None, None)  # Auto-size
        # Set Y-axis decimal precision
        self.plt.yfrequency(0)  # This will auto-determine the frequency
    
    def add_data(self, series_name: str, value: float, y_axis: str = "left", color: str | None = None, render: bool = True):
        """Add data point to a specific series"""
        current_time = time.time()
        
        # Initialize series if it doesn't exist
        if series_name not in self.data_series:
            # Use provided color or default to the chart's color
            series_color = color if color else self.plot_color
            self.data_series[series_name] = {
                'data': deque(maxlen=self.max_points),
                'timestamps': deque(maxlen=self.max_points),
                'y_axis': y_axis,
                'color': series_color
            }
        
        self.data_series[series_name]['data'].append(value)
        self.data_series[series_name]['timestamps'].append(current_time)
        if not render:
            return
        last_value = self._last_values.get(series_name)
        if last_value is not None and abs(value - last_value) < 0.1:
            return
        self._last_values[series_name] = value
        
        self.plt.clear_data()
        
        # Determine y-axis range based on all data
        all_data_left = []
        all_data_right = []
        
        for name, series in self.data_series.items():
            if series['y_axis'] == 'left':
                all_data_left.extend(list(series['data']))
            else:
                all_data_right.extend(list(series['data']))
        
        # Set up left y-axis (usage percentage)
        if all_data_left:
            y_min_left = 0
            y_max_left = max(max(all_data_left), 100)  # At least 100 for percentage
            y_ticks_left = [0, 25, 50, 75, 100] if y_max_left <= 100 else [0, y_max_left/4, y_max_left/2, 3*y_max_left/4, y_max_left]
            y_labels_left = [f"{val:.1f}" for val in y_ticks_left]
            self.plt.yticks(y_ticks_left, y_labels_left)
            self.plt.ylim(0, y_max_left * 1.1)
        
        # Set x-axis to show 0.0 to 0.6 minutes ago by default
        default_x_ticks = [0.0, -0.1, -0.2, -0.3, -0.4, -0.5, -0.6]
        default_x_labels = ["0.0", "0.1", "0.2", "0.3", "0.4", "0.5", "0.6"]
        
        # Plot all series
        has_data = False
        for name, series in self.data_series.items():
            if len(series['data']) > 1:
                has_data = True
                # Calculate time differences from now in minutes
                time_diffs = [(current_time - t) / 60 for t in series['timestamps']]
                # Reverse so most recent is on the right
                time_diffs = [-td for td in time_diffs]
                
                # Use series-specific color
                self.plt.plot(time_diffs, list(series['data']), marker="braille", color=series['color'], label=name)
        
        if has_data:
            # Use the most recent series' timestamps for x-axis
            most_recent_series = max(self.data_series.values(), key=lambda s: len(s['data']))
            time_diffs = [(current_time - t) / 60 for t in most_recent_series['timestamps']]
            time_diffs = [-td for td in time_diffs]
            
            # Set x-axis labels - show actual time values
            if len(time_diffs) >= 5:
                # Show 5 evenly spaced labels
                indices = [0, len(time_diffs)//4, len(time_diffs)//2, 3*len(time_diffs)//4, len(time_diffs)-1]
                ticks = [time_diffs[i] for i in indices]
                labels = [f"{abs(t):.1f}" for t in ticks]
                self.plt.xticks(ticks, labels)
            else:
                # For fewer points, show all
                labels = [f"{abs(t):.1f}" for t in time_diffs]
                self.plt.xticks(time_diffs, labels)
        else:
            # No data yet, show default x-axis
            self.plt.xticks(default_x_ticks, default_x_labels)
            self.plt.xlim(-0.6, 0)
        
        self.refresh()
    
    def update_title(self, title: str):
        self.title = title
        self.plt.title(title)
        self.refresh()


class FluidTopApp(App):
    """Main FluidTop application using Textual"""
    
    # CSS is set dynamically in _apply_theme method
    
    def __init__(self, interval: int, theme: str, avg: int, max_count: int, show_cores: bool = False, thermal_bell: bool = False):
        self.interval = interval
        # Store theme temporarily, don't assign to self.theme yet
        theme_value = theme
        self.theme_colors = self._get_theme_colors(theme_value)
        # Apply theme BEFORE calling super().__init__()
        self._apply_theme(theme_value)
        
        super().__init__()
        
        # Store theme value in a regular instance variable (not reactive)
        self._theme_name = theme_value
        self.avg = avg
        self.max_count = max_count
        self.show_cores = show_cores
        self.thermal_bell = thermal_bell
        
        # Initialize metrics storage
        # No longer tracking averages or peaks
        
        # Total energy consumption tracking (in watt-seconds) for each component
        self.total_energy_consumed = 0
        self.cpu_energy_consumed = 0
        self.gpu_energy_consumed = 0
        self.ane_energy_consumed = 0
        
        # Powermetrics process
        self.powermetrics_process = None
        self.timecode = None
        self.last_timestamp = 0
        self.count = 0
        self.last_thermal_pressure = None
        self.session_start = time.time()
        self.summary_samples = 0
        self.cpu_usage_sum = 0.0
        self.gpu_usage_sum = 0.0
        self.ram_usage_sum = 0.0
        self.cpu_usage_peak = 0.0
        self.gpu_usage_peak = 0.0
        self.ram_usage_peak = 0.0
        self.cpu_power_peak = 0.0
        self.gpu_power_peak = 0.0
        self.ane_power_peak = 0.0
        self.thermal_pressure_max = "Nominal"
        
        # SoC info
        self.soc_info_dict = get_soc_info()
        
        # Smoothing buffers for CPU usage
        self.cpu_usage_buffer_size = 5  # Average over last 5 samples
        self.e_cpu_usage_buffer = []
        self.p_cpu_usage_buffer = []
        self._render_every_n = 2
        self._render_tick = 0
        self._core_usage_buffer = {}
        
    def _get_theme_colors(self, theme: str) -> str:
        """Get the color mapping for the theme using plotext-compatible color names"""
        # Using plotext-compatible color names instead of hex colors
        theme_chart_colors = {
            'default': 'gray',
            'dark': 'white', 
            'blue': 'blue',
            'green': 'green',
            'red': 'red',
            'purple': 'magenta', 
            'orange': 'yellow',
            'cyan': 'cyan',
            'magenta': 'magenta'
        }
        return theme_chart_colors.get(theme, 'cyan')
        
    def _apply_theme(self, theme: str):
        """Apply color theme to the application"""
        # Using shadcn-inspired hex colors for better design consistency
        themes = {
            'default': {'primary': '#18181b', 'accent': '#71717a'},  # zinc-900, zinc-500
            'dark': {'primary': '#fafafa', 'accent': '#a1a1aa'},     # zinc-50, zinc-400
            'blue': {'primary': '#1e40af', 'accent': '#3b82f6'},     # blue-800, blue-500
            'green': {'primary': '#166534', 'accent': '#22c55e'},    # green-800, green-500
            'red': {'primary': '#dc2626', 'accent': '#ef4444'},      # red-600, red-500
            'purple': {'primary': '#7c3aed', 'accent': '#a855f7'},   # violet-600, purple-500
            'orange': {'primary': '#FD8161', 'accent': '#f97316'},   # orange-600, orange-500
            'cyan': {'primary': '#5DAF8D', 'accent': '#06b6d4'},     # cyan-600, cyan-500
            'magenta': {'primary': '#db2777', 'accent': '#ec4899'}   # pink-600, pink-500
        }
        
        if theme in themes:
            colors = themes[theme]
            # Update CSS with theme colors and reduced padding
            self.CSS = f"""
    Screen {{
        layers: base;
        overflow: hidden hidden;
    }}
    
    Screen > Container {{
        width: 100%;
        height: 100%;
        overflow: hidden hidden;
    }}
    
    MetricGauge {{
        height: 3;
        margin: 0;
        border: solid {colors['primary']};
    }}
    
    PowerChart {{
        height: 1fr;
        margin: 0;
        border: none;
        background: $surface;
    }}
    
    PowerChart PlotextPlot {{
        background: $surface;
    }}
    
    UsageChart {{
        height: 1fr;
        margin: 0;
        border: none;
        background: $surface;
    }}
    
    UsageChart PlotextPlot {{
        background: $surface;
    }}
    
    MultiLineChart {{
        height: 1fr;
        margin: 0;
        border: none;
        background: $surface;
    }}
    
    MultiLineChart PlotextPlot {{
        background: $surface;
    }}
    
    #usage-section {{
        border: solid {colors['primary']};
        padding: 0;
        height: 1fr;
        background: $surface;
    }}
    
    #power-section {{
        border: solid {colors['primary']};
        padding: 0;
        height: 1fr;
        background: $surface;
    }}
    
    #power-section Horizontal {{
        margin-bottom: 1;
    }}
    
    #power-section Horizontal:last-child {{
        margin-bottom: 0;
    }}
    
    #controls-section {{
        border: solid {colors['accent']};
        padding: 0 1;
        height: 3;
        background: $surface;
    }}
    
    #controls-content {{
        layout: horizontal;
        align: center middle;
        width: 100%;
        height: 100%;
    }}
    
    #system-info-label {{
        width: 2fr;
        text-align: left;
        color: $text;
        padding: 0 1;
    }}
    
    #controls-buttons {{
        width: 1fr;
        layout: horizontal;
        align: right middle;
        height: 100%;
    }}
    
    #thermal-label {{
        width: auto;
        text-align: right;
        color: $text;
        padding: 0 1;
    }}
    
    #timestamp-label {{
        width: auto;
        text-align: right;
        color: $text;
        padding: 0 1;
    }}
    
    Label {{
        color: $text;
        margin: 0;
        padding: 0;
    }}
    
    #controls-title {{
        text-style: bold;
        margin: 0;
        padding: 0;
    }}
    """
        
    def compose(self) -> ComposeResult:
        """Compose the UI layout"""
        
        # Controls section with power and system info at the top
        with Vertical(id="controls-section"):
            with Horizontal(id="controls-content"):
                # System info on the left
                yield Label("Initializing...", id="system-info-label")
                # Timestamp on the right
                with Horizontal(id="controls-buttons"):
                    yield Label("Thermal Pressure: --", id="thermal-label")
                    yield Label("", id="timestamp-label")
        
        # Usage Charts section
        with Vertical(id="usage-section"):
            with Horizontal():
                yield MultiLineChart("CPU Usage (E-CPU & P-CPU)", ylabel="Usage (%)", interval=self.interval, color=self.theme_colors, id="cpu-combined-chart")
                yield UsageChart("GPU", interval=self.interval, color=self.theme_colors, id="gpu-usage-chart")
                yield UsageChart("RAM Usage", ylabel="RAM (%)", interval=self.interval, color=self.theme_colors, id="ram-usage-chart")
        if self.show_cores:
            with Vertical(id="core-usage-section"):
                with Horizontal():
                    yield MultiLineChart("E-Core Usage", ylabel="Usage (%)", interval=self.interval, color="blue", id="e-core-usage-chart")
                    yield MultiLineChart("P-Core Usage", ylabel="Usage (%)", interval=self.interval, color="red", id="p-core-usage-chart")
        
        # Power section
        with Vertical(id="power-section"):
            with Horizontal():
                yield PowerChart("CPU Power", interval=self.interval, color=self.theme_colors, id="cpu-power-chart")
                yield PowerChart("GPU Power", interval=self.interval, color=self.theme_colors, id="gpu-power-chart")
                yield PowerChart("ANE Power", interval=self.interval, color=self.theme_colors, id="ane-power-chart")
    
    async def on_mount(self):
        """Initialize the application on mount"""
        # Start powermetrics process
        self.timecode = str(int(time.time()))
        self.powermetrics_process = run_powermetrics_process(
            self.timecode, interval=int(self.interval * 1000)
        )
        
        # Wait for first reading
        await self.wait_for_first_reading()
        
        # Start update timer
        self.set_interval(self.interval, self.update_metrics)
        
        # Update system info label
        system_info = f"{self.soc_info_dict['name']} ({self.soc_info_dict['e_core_count']}E+{self.soc_info_dict['p_core_count']}P+{self.soc_info_dict['gpu_core_count']}GPU)"
        self.query_one("#system-info-label", Label).update(system_info)
        
        # Initialize timestamp
        await self.update_timestamp()
    
    async def wait_for_first_reading(self):
        """Wait for the first powermetrics reading"""
        while True:
            ready = parse_powermetrics(timecode=self.timecode)
            if ready:
                self.last_timestamp = ready[-1]
                break
            await asyncio.sleep(0.1)
    
    async def update_metrics(self):
        """Update all metrics - called by timer"""
        try:
            # Handle max_count restart
            if self.max_count > 0 and self.count >= self.max_count:
                self.count = 0
                self.powermetrics_process.terminate()
                self.timecode = str(int(time.time()))
                self.powermetrics_process = run_powermetrics_process(
                    self.timecode, interval=int(self.interval * 1000)
                )
            self.count += 1
            
            # Parse powermetrics data
            ready = parse_powermetrics(timecode=self.timecode)
            if not ready:
                return
                
            cpu_metrics_dict, gpu_metrics_dict, thermal_pressure, _, timestamp = ready
            
            if timestamp <= self.last_timestamp:
                return
                
            self.last_timestamp = timestamp
            
            # CPU, GPU, and ANE gauge widgets have been removed
            
            self._render_tick += 1
            should_render = (self._render_tick % self._render_every_n) == 0
            # Update usage charts
            await self.update_usage_charts(cpu_metrics_dict, gpu_metrics_dict, should_render)
            
            # Update power charts
            await self.update_power_charts(cpu_metrics_dict, thermal_pressure, should_render)
            
            # Update timestamp
            await self.update_timestamp()
            
        except Exception as e:
            # Handle errors gracefully
            pass
    
    async def update_usage_charts(self, cpu_metrics_dict, gpu_metrics_dict, should_render: bool):
        """Update usage chart metrics"""
        # Update combined CPU chart (E-CPU and P-CPU)
        cpu_combined_chart = self.query_one("#cpu-combined-chart", MultiLineChart)
        
        # Get E-CPU and P-CPU usage data
        e_cpu_usage_raw = cpu_metrics_dict['E-Cluster_active']
        p_cpu_usage_raw = cpu_metrics_dict['P-Cluster_active']
        
        # Add to smoothing buffers
        self.e_cpu_usage_buffer.append(e_cpu_usage_raw)
        self.p_cpu_usage_buffer.append(p_cpu_usage_raw)
        
        # Keep buffer size limited
        if len(self.e_cpu_usage_buffer) > self.cpu_usage_buffer_size:
            self.e_cpu_usage_buffer.pop(0)
        if len(self.p_cpu_usage_buffer) > self.cpu_usage_buffer_size:
            self.p_cpu_usage_buffer.pop(0)
        
        # Calculate smoothed values (average of buffer)
        e_cpu_usage = int(sum(self.e_cpu_usage_buffer) / len(self.e_cpu_usage_buffer))
        p_cpu_usage = int(sum(self.p_cpu_usage_buffer) / len(self.p_cpu_usage_buffer))
        
        # Add both CPU types to the same chart with different colors
        cpu_combined_chart.add_data(
            f"E-CPU ({self.soc_info_dict['e_core_count']} cores)",
            e_cpu_usage,
            y_axis="left",
            color="blue",
            render=should_render,
        )
        cpu_combined_chart.add_data(
            f"P-CPU ({self.soc_info_dict['p_core_count']} cores)",
            p_cpu_usage,
            y_axis="left",
            color="red",
            render=should_render,
        )
        
        # Update title to show both CPU types (smoothed values)
        combined_title = f"E-CPU: {e_cpu_usage}% | P-CPU: {p_cpu_usage}%"
        cpu_combined_chart.update_title(combined_title)
        combined_cpu_usage = (e_cpu_usage + p_cpu_usage) / 2
        
        # Update GPU usage chart
        gpu_chart = self.query_one("#gpu-usage-chart", UsageChart)
        gpu_usage = gpu_metrics_dict['active']
        gpu_title = f"GPU ({self.soc_info_dict['gpu_core_count']} cores): {gpu_usage}%"
        gpu_chart.update_title(gpu_title)
        gpu_chart.add_data(gpu_usage, render=should_render)
        
        # Update RAM usage chart with swap information
        ram_metrics_dict = get_ram_metrics_dict()
        ram_chart = self.query_one("#ram-usage-chart", UsageChart)
        ram_usage_percent = ram_metrics_dict["used_percent"]
        
        # Include swap information in the title
        if ram_metrics_dict["swap_total_GB"] < 0.1:
            ram_title = f"RAM: {ram_usage_percent:.1f}% ({ram_metrics_dict['used_GB']:.1f}/{ram_metrics_dict['total_GB']:.1f}GB) - swap inactive"
        else:
            ram_title = f"RAM: {ram_usage_percent:.1f}% ({ram_metrics_dict['used_GB']:.1f}/{ram_metrics_dict['total_GB']:.1f}GB) - swap: {ram_metrics_dict['swap_used_GB']:.1f}/{ram_metrics_dict['swap_total_GB']:.1f}GB"
        
        ram_chart.update_title(ram_title)
        ram_chart.add_data(ram_usage_percent, render=should_render)

        self.summary_samples += 1
        self.cpu_usage_sum += combined_cpu_usage
        self.gpu_usage_sum += gpu_usage
        self.ram_usage_sum += ram_usage_percent
        self.cpu_usage_peak = max(self.cpu_usage_peak, combined_cpu_usage)
        self.gpu_usage_peak = max(self.gpu_usage_peak, gpu_usage)
        self.ram_usage_peak = max(self.ram_usage_peak, ram_usage_percent)

        if self.show_cores:
            await self.update_core_usage_charts(cpu_metrics_dict, should_render)

    async def update_core_usage_charts(self, cpu_metrics_dict, should_render: bool):
        """Update per-core usage charts (optional)"""
        e_core_chart = self.query_one("#e-core-usage-chart", MultiLineChart)
        p_core_chart = self.query_one("#p-core-usage-chart", MultiLineChart)

        for core_id in cpu_metrics_dict.get("e_core", []):
            key = f"E{core_id}"
            metric_key = f"E-Cluster{core_id}_active"
            core_value = cpu_metrics_dict.get(metric_key)
            if core_value is None:
                continue
            self._core_usage_buffer.setdefault(key, []).append(core_value)
            if len(self._core_usage_buffer[key]) > self.cpu_usage_buffer_size:
                self._core_usage_buffer[key].pop(0)
            smoothed = int(sum(self._core_usage_buffer[key]) / len(self._core_usage_buffer[key]))
            e_core_chart.add_data(key, smoothed, y_axis="left", color="blue", render=should_render)

        for core_id in cpu_metrics_dict.get("p_core", []):
            key = f"P{core_id}"
            metric_key = f"P-Cluster{core_id}_active"
            core_value = cpu_metrics_dict.get(metric_key)
            if core_value is None:
                continue
            self._core_usage_buffer.setdefault(key, []).append(core_value)
            if len(self._core_usage_buffer[key]) > self.cpu_usage_buffer_size:
                self._core_usage_buffer[key].pop(0)
            smoothed = int(sum(self._core_usage_buffer[key]) / len(self._core_usage_buffer[key]))
            p_core_chart.add_data(key, smoothed, y_axis="left", color="red", render=should_render)
    
    async def update_power_charts(self, cpu_metrics_dict, thermal_pressure, should_render: bool):
        """Update power chart metrics"""
        cpu_max_power = self.soc_info_dict["cpu_max_power"]
        gpu_max_power = self.soc_info_dict["gpu_max_power"]
        ane_max_power = 8.0
        
        # Calculate power values (already in watts from powermetrics)
        package_power_W = cpu_metrics_dict["package_W"]
        cpu_power_W = cpu_metrics_dict["cpu_W"]
        gpu_power_W = cpu_metrics_dict["gpu_W"]
        ane_power_W = cpu_metrics_dict["ane_W"]

        self.cpu_power_peak = max(self.cpu_power_peak, cpu_power_W)
        self.gpu_power_peak = max(self.gpu_power_peak, gpu_power_W)
        self.ane_power_peak = max(self.ane_power_peak, ane_power_W)
        
        # Update energy consumption for each component (watts * seconds = watt-seconds)
        self.total_energy_consumed += package_power_W * self.interval
        self.cpu_energy_consumed += cpu_power_W * self.interval
        self.gpu_energy_consumed += gpu_power_W * self.interval
        self.ane_energy_consumed += ane_power_W * self.interval
        
        # Helper function to format energy display
        def format_energy(energy_ws):
            energy_wh = energy_ws / 3600  # Convert watt-seconds to watt-hours
            energy_mwh = energy_wh * 1000  # Convert to milliwatt-hours
            
            if energy_mwh < 0.01:
                # For very small values, show in scientific notation or as <0.01mWh
                return "<0.01mWh" if energy_mwh > 0 else "0.00mWh"
            elif energy_mwh < 10:
                # For small values, use 2 decimal points
                return f"{energy_mwh:.2f}mWh"
            elif energy_wh < 1.0:
                # For values under 1Wh, use 1 decimal point
                return f"{energy_mwh:.1f}mWh"
            elif energy_wh < 1000:
                return f"{energy_wh:.2f}Wh"
            else:
                return f"{energy_wh / 1000:.3f}kWh"
        
        # Update charts
        cpu_power_chart = self.query_one("#cpu-power-chart", PowerChart)
        cpu_power_percent = cpu_power_W / cpu_max_power * 100  # Keep as float
        cpu_energy_display = format_energy(self.cpu_energy_consumed)
        cpu_title = f"CPU: {cpu_power_W:.2f}W (total: {cpu_energy_display})"
        cpu_power_chart.update_title(cpu_title)
        cpu_power_chart.add_data(cpu_power_percent, render=should_render)
        
        gpu_power_chart = self.query_one("#gpu-power-chart", PowerChart)
        gpu_power_percent = gpu_power_W / gpu_max_power * 100  # Keep as float
        gpu_energy_display = format_energy(self.gpu_energy_consumed)
        gpu_title = f"GPU: {gpu_power_W:.2f}W (total: {gpu_energy_display})"
        gpu_power_chart.update_title(gpu_title)
        gpu_power_chart.add_data(gpu_power_percent, render=should_render)
        
        ane_power_chart = self.query_one("#ane-power-chart", PowerChart)
        ane_power_percent = ane_power_W / ane_max_power * 100  # Keep as float
        ane_energy_display = format_energy(self.ane_energy_consumed)
        ane_title = f"ANE: {ane_power_W:.2f}W (total: {ane_energy_display})"
        ane_power_chart.update_title(ane_title)
        ane_power_chart.add_data(ane_power_percent, render=should_render)
        
        # Update system info label with total power and thermal info
        total_energy_display = format_energy(self.total_energy_consumed)
        system_info = f"{self.soc_info_dict['name']} ({self.soc_info_dict['e_core_count']}E+{self.soc_info_dict['p_core_count']}P+{self.soc_info_dict['gpu_core_count']}GPU) | Total: {package_power_W:.1f}W ({total_energy_display})"
        self.query_one("#system-info-label", Label).update(system_info)
        self.query_one("#thermal-label", Label).update(f"Thermal Pressure: {thermal_pressure}")
        thermal_levels = ["Nominal", "Moderate", "Heavy", "Critical"]
        if thermal_pressure in thermal_levels and thermal_levels.index(thermal_pressure) > thermal_levels.index(self.thermal_pressure_max):
            self.thermal_pressure_max = thermal_pressure
        if self.thermal_bell and thermal_pressure != "Nominal" and thermal_pressure != self.last_thermal_pressure:
            try:
                print("\a", end="", flush=True)
            except Exception:
                pass
        self.last_thermal_pressure = thermal_pressure
    
    async def update_timestamp(self):
        """Update the timestamp display"""
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        timestamp_label = self.query_one("#timestamp-label", Label)
        timestamp_label.update(f"📅 {current_time}")
    
    
    def on_unmount(self):
        """Clean up when app is closed"""
        if self.powermetrics_process:
            try:
                self.powermetrics_process.terminate()
            except:
                pass
        duration_sec = max(0, time.time() - self.session_start)
        if self.summary_samples > 0:
            avg_cpu = self.cpu_usage_sum / self.summary_samples
            avg_gpu = self.gpu_usage_sum / self.summary_samples
            avg_ram = self.ram_usage_sum / self.summary_samples
        else:
            avg_cpu = avg_gpu = avg_ram = 0.0
        summary = {
            "duration_sec": round(duration_sec, 1),
            "avg_cpu_percent": round(avg_cpu, 2),
            "avg_gpu_percent": round(avg_gpu, 2),
            "avg_ram_percent": round(avg_ram, 2),
            "peak_cpu_percent": round(self.cpu_usage_peak, 2),
            "peak_gpu_percent": round(self.gpu_usage_peak, 2),
            "peak_ram_percent": round(self.ram_usage_peak, 2),
            "peak_cpu_w": round(self.cpu_power_peak, 2),
            "peak_gpu_w": round(self.gpu_power_peak, 2),
            "peak_ane_w": round(self.ane_power_peak, 2),
            "total_energy_wh": round(self.total_energy_consumed / 3600, 3),
            "thermal_pressure_max": self.thermal_pressure_max,
        }
        print("\nSession summary:\n" + json.dumps(summary, indent=2))

@click.command()
@click.option('--interval', type=float, default=1.0,
              help='Display interval and sampling interval for powermetrics (seconds)')
@click.option('--theme', type=click.Choice(['default', 'dark', 'blue', 'green', 'red', 'purple', 'orange', 'cyan', 'magenta']), default='cyan',
              help='Choose color theme')
@click.option('--avg', type=int, default=30,
              help='Interval for averaged values (seconds)')
@click.option('--max_count', type=int, default=0,
              help='Max show count to restart powermetrics')
@click.option('--show_cores', is_flag=True, default=False,
              help='Show per-core CPU usage charts')
@click.option('--thermal_bell', is_flag=True, default=False,
              help='Ring terminal bell when thermal throttling is detected')
def main(interval, theme, avg, max_count, show_cores, thermal_bell):
    """msiltop: Performance monitoring CLI tool for Apple Silicon"""
    return _main_logic(interval, theme, avg, max_count, show_cores=show_cores, thermal_bell=thermal_bell)


def _main_logic(interval, theme, avg, max_count, show_cores=False, thermal_bell=False):
    """Main logic using Textual app"""
    print("\nMSILTOP - Performance monitoring CLI tool for Apple Silicon")
    print("Get help at `https://github.com/pratikdevnani/msiltop`")
    print("P.S. You are recommended to run MSILTOP with `sudo msiltop`\n")
    
    # Create and run the Textual app
    app = FluidTopApp(interval, theme, avg, max_count, show_cores=show_cores, thermal_bell=thermal_bell)
    try:
        app.run()
    except KeyboardInterrupt:
        print("Stopping...")
    finally:
        # Cleanup is handled in app.on_unmount()
        pass
    
    return app.powermetrics_process


if __name__ == "__main__":
    powermetrics_process = main()
    try:
        powermetrics_process.terminate()
        print("Successfully terminated powermetrics process")
    except Exception as e:
        print(e)
        powermetrics_process.terminate()
        print("Successfully terminated powermetrics process")
