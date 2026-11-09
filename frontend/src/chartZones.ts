import type { AutoscaleInfo, IPrimitivePaneRenderer, IPrimitivePaneView, ISeriesApi, ISeriesPrimitive, SeriesAttachedParameter, SeriesType, Time } from "lightweight-charts";

/** A shaded price band across the chart: a buy zone, a take-profit zone, a support or resistance. */
export type Band = { low: number; high: number; fill: string; edge: string; label: string; dashed?: boolean; key?: boolean };

type Target = Parameters<IPrimitivePaneRenderer["draw"]>[0];

/**
 * Draws bands behind the candles of the series it is attached to. Lightweight
 * Charts has price lines but no price ranges, so a zone is painted with the
 * series' own price-to-pixel mapping on every redraw. Key bands (the plan's buy
 * and take-profit zones) widen the autoscale so they stay in view.
 */
export class BandsPrimitive implements ISeriesPrimitive<Time> {
  private series: ISeriesApi<SeriesType> | null = null;
  private readonly views: readonly IPrimitivePaneView[];

  constructor(private readonly bands: Band[]) {
    const renderer: IPrimitivePaneRenderer = { draw: () => undefined, drawBackground: (target) => this.paint(target) };
    this.views = [{ zOrder: () => "bottom", renderer: () => renderer }];
  }

  attached(param: SeriesAttachedParameter<Time>) { this.series = param.series; }
  detached() { this.series = null; }
  paneViews() { return this.views; }

  autoscaleInfo(): AutoscaleInfo | null {
    const key = this.bands.filter((band) => band.key);
    if (!key.length) return null;
    return { priceRange: { minValue: Math.min(...key.map((band) => band.low)), maxValue: Math.max(...key.map((band) => band.high)) } };
  }

  private paint(target: Target) {
    const series = this.series;
    if (!series) return;
    target.useBitmapCoordinateSpace(({ context, bitmapSize, horizontalPixelRatio: h, verticalPixelRatio: v }) => {
      context.save();
      for (const band of this.bands) {
        const top = series.priceToCoordinate(band.high), bottom = series.priceToCoordinate(band.low);
        if (top === null || bottom === null) continue;
        const y = Math.round(Math.min(top, bottom) * v), height = Math.max(Math.round(Math.abs(bottom - top) * v), Math.round(2 * v));
        if (y > bitmapSize.height || y + height < 0) continue;
        context.fillStyle = band.fill;
        context.fillRect(0, y, bitmapSize.width, height);
        context.strokeStyle = band.edge;
        context.lineWidth = Math.max(1, Math.round(v));
        context.setLineDash(band.dashed ? [Math.round(4 * h), Math.round(4 * h)] : []);
        context.beginPath();
        for (const edge of [y + 0.5, y + height - 0.5]) { context.moveTo(0, edge); context.lineTo(bitmapSize.width, edge); }
        context.stroke();
        context.setLineDash([]);
        context.font = `600 ${Math.round(10.5 * v)}px Manrope, system-ui, sans-serif`;
        context.fillStyle = band.edge;
        context.textBaseline = "top";
        // Inside a band tall enough for a line of text; a thin key band labels
        // just above itself, and a thin background zone goes unlabelled.
        const inside = height > 16 * v;
        if (inside || band.key) context.fillText(band.label, Math.round(8 * h), inside ? y + Math.round(4 * v) : y - Math.round(13 * v));
      }
      context.restore();
    });
  }
}
