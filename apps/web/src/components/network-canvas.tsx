"use client";

import dynamic from "next/dynamic";
import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef } from "react";
import type { GraphResponse } from "@course-kg/shared";
import type { ECharts, EChartsOption, SetOptionOpts } from "echarts";
import type { ComponentType } from "react";
import type { EChartsReactProps } from "echarts-for-react";

const ReactECharts = dynamic(
  () => import("echarts-for-react").then((module) => module.default as unknown as ComponentType<EChartsReactProps>),
  { ssr: false },
);

const palette: Record<string, string> = {
  course: "#a5e9ff",
  chapter: "#8f97ff",
  document: "#6be2bf",
  concept: "#63cbff",
};

export type NetworkCanvasHandle = {
  resetView: () => void;
  fitView: () => void;
  toggleLayoutLock: () => boolean;
};

type NetworkCanvasProps = {
  graph: GraphResponse;
  height?: number | string;
  selectedNodeId?: string | null;
  onNodeClick?: (nodeId: string, category: string) => void;
  onNodeDoubleClick?: (nodeId: string, category: string) => void;
};

type NodePosition = readonly [number, number];
type NodePositionMap = Map<string, NodePosition>;

type RuntimeForceLayout = {
  warmUp: () => void;
  setFixed: (idx: number) => void;
  setUnfixed: (idx: number) => void;
};

type RuntimeSeriesData = {
  getItemLayout?: (dataIndex: number) => unknown;
  setItemLayout?: (dataIndex: number, layout: NodePosition) => void;
};

type RuntimeGraphNode = {
  setLayout: (layout: NodePosition) => void;
};

type RuntimeGraph = {
  getNodeByIndex: (dataIndex: number) => RuntimeGraphNode;
};

type RuntimeSeriesModel = {
  getData?: () => RuntimeSeriesData;
  get?: (path: string | string[]) => unknown;
  getGraph?: () => RuntimeGraph;
  forceLayout?: RuntimeForceLayout | null;
};

type RuntimeGlobalModel = {
  getSeriesByIndex?: (seriesIndex: number) => RuntimeSeriesModel | undefined;
};

type RuntimeGraphView = {
  updateLayout: (seriesModel: RuntimeSeriesModel) => void;
  _layouting?: boolean;
  _layoutTimeout?: ReturnType<typeof setTimeout> | null;
  _startForceLayoutIteration?: (forceLayout: RuntimeForceLayout, api: ECharts, layoutAnimation: boolean) => void;
};

type RuntimeChart = {
  getModel?: () => RuntimeGlobalModel;
  getViewOfSeriesModel?: (seriesModel: RuntimeSeriesModel) => RuntimeGraphView | undefined;
};

function buildBaseOption(graph: GraphResponse): EChartsOption {
  return {
    animationDuration: 420,
    animationEasing: "cubicOut",
    backgroundColor: "transparent",
    tooltip: {
      backgroundColor: "rgba(4, 10, 28, 0.94)",
      borderColor: "rgba(120, 215, 255, 0.18)",
      textStyle: { color: "#edf6ff" },
    },
    series: [
      {
        type: "graph",
        roam: true,
        roamTrigger: "global",
        left: 0,
        top: 0,
        right: 0,
        bottom: 0,
        layout: "force",
        force: {
          initLayout: "none",
          repulsion: 160,
          edgeLength: [90, 150],
          gravity: 0.03,
          friction: 0.14,
          layoutAnimation: true,
        },
        draggable: true,
        label: {
          show: true,
          color: "#dff7ff",
          fontSize: 11,
          distance: 6,
          overflow: "break",
          width: 140,
        },
        lineStyle: {
          color: "rgba(122, 169, 255, 0.16)",
          width: 1,
          curveness: 0.06,
          opacity: 0.72,
        },
        emphasis: {
          focus: "none",
          scale: 1.04,
          itemStyle: {
            borderWidth: 2,
            borderColor: "rgba(227,248,255,0.95)",
            shadowBlur: 16,
            shadowColor: "rgba(126, 226, 255, 0.18)",
          },
          lineStyle: {
            width: 1.25,
            opacity: 0.78,
          },
          label: {
            color: "#ffffff",
          },
        },
        blur: {
          itemStyle: { opacity: 1 },
          lineStyle: { opacity: 0.72 },
          label: { opacity: 1 },
        },
        edgeLabel: { show: false },
        data: graph.nodes.map((node) => ({
          ...node,
          draggable: true,
          fixed: false,
          symbolSize:
            node.category === "concept"
              ? 16 + Math.min(14, (node.value ?? 2) * 0.85)
              : node.category === "document"
                ? 16
                : 20,
          itemStyle: {
            color: palette[node.category] ?? "#63cbff",
            borderWidth: 0.8,
            borderColor: "rgba(255,255,255,0.14)",
            shadowBlur: 7,
            shadowColor: "rgba(99, 203, 255, 0.08)",
          },
          label: {
            color: "#dff7ff",
          },
        })),
        links: graph.edges.map((edge) => ({
          source: edge.source,
          target: edge.target,
          relationLabel: edge.label,
          confidence: edge.confidence,
          category: edge.category,
          evidence_chunk_id: edge.evidence_chunk_id,
          lineStyle: {
            color: edge.category === "semantic" ? "rgba(84, 213, 255, 0.16)" : "rgba(155, 165, 255, 0.11)",
            width: edge.category === "semantic" ? 1.05 : 0.8,
          },
        })),
      },
    ],
  };
}

export const NetworkCanvas = forwardRef<NetworkCanvasHandle, NetworkCanvasProps>(function NetworkCanvas(
  { graph, height = 620, selectedNodeId = null, onNodeClick, onNodeDoubleClick },
  ref,
) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<ECharts | null>(null);
  const isLockedRef = useRef(false);
  const highlightedNodeRef = useRef<string | null>(null);

  const option = useMemo(() => buildBaseOption(graph), [graph]);

  const setOption = useCallback((nextOption: EChartsOption, opts?: SetOptionOpts) => {
    const instance = chartRef.current;
    if (!instance) {
      return;
    }
    instance.setOption(nextOption, opts);
  }, []);

  const resizeChart = useCallback(() => {
    const instance = chartRef.current;
    const host = hostRef.current;
    if (!instance || !host) {
      return;
    }

    const { width, height: hostHeight } = host.getBoundingClientRect();
    if (width <= 0 || hostHeight <= 0) {
      return;
    }

    instance.resize({ width, height: hostHeight });
  }, []);

  const getCurrentNodePositions = useCallback((): NodePositionMap => {
    const positions: NodePositionMap = new Map();
    const instance = chartRef.current;
    if (!instance) {
      return positions;
    }

    const seriesData = (instance as unknown as RuntimeChart).getModel?.().getSeriesByIndex?.(0)?.getData?.();
    if (!seriesData?.getItemLayout) {
      return positions;
    }

    graph.nodes.forEach((node, index) => {
      const layout = seriesData.getItemLayout?.(index);
      if (!Array.isArray(layout)) {
        return;
      }
      const [x, y] = layout;
      if (typeof x === "number" && typeof y === "number" && Number.isFinite(x) && Number.isFinite(y)) {
        positions.set(node.id, [x, y]);
      }
    });

    return positions;
  }, [graph.nodes]);

  const getRuntimeState = useCallback(() => {
    const instance = chartRef.current;
    const runtimeChart = instance as unknown as RuntimeChart | null;
    const seriesModel = runtimeChart?.getModel?.().getSeriesByIndex?.(0);
    const graphView = seriesModel ? runtimeChart?.getViewOfSeriesModel?.(seriesModel) : undefined;
    const forceLayout = seriesModel?.forceLayout ?? undefined;
    const data = seriesModel?.getData?.();
    const runtimeGraph = seriesModel?.getGraph?.();

    if (!instance || !seriesModel || !graphView || !forceLayout || !data || !runtimeGraph) {
      return null;
    }

    return { instance, seriesModel, graphView, forceLayout, data, runtimeGraph };
  }, []);

  const syncHighlight = useCallback(
    (nodeId: string | null) => {
      const instance = chartRef.current;
      if (!instance) {
        return;
      }

      const previousId = highlightedNodeRef.current;
      if (previousId) {
        const previousIndex = graph.nodes.findIndex((node) => node.id === previousId);
        if (previousIndex >= 0) {
          instance.dispatchAction({ type: "downplay", seriesIndex: 0, dataIndex: previousIndex });
        }
      }

      if (nodeId) {
        const nextIndex = graph.nodes.findIndex((node) => node.id === nodeId);
        if (nextIndex >= 0) {
          instance.dispatchAction({ type: "highlight", seriesIndex: 0, dataIndex: nextIndex });
        }
      }

      highlightedNodeRef.current = nodeId;
    },
    [graph.nodes],
  );

  const resetView = useCallback(() => {
    isLockedRef.current = false;
    setOption(buildBaseOption(graph), { replaceMerge: ["series"] });
    highlightedNodeRef.current = null;
    requestAnimationFrame(() => {
      resizeChart();
      syncHighlight(selectedNodeId);
    });
  }, [graph, resizeChart, selectedNodeId, setOption, syncHighlight]);

  const fitView = useCallback(() => {
    const instance = chartRef.current;
    if (!instance) {
      return;
    }
    resizeChart();
    instance.dispatchAction({ type: "restore" });
    requestAnimationFrame(() => syncHighlight(selectedNodeId));
  }, [resizeChart, selectedNodeId, syncHighlight]);

  const toggleLayoutLock = useCallback(() => {
    const nextLocked = !isLockedRef.current;
    const runtimeState = getRuntimeState();
    if (!runtimeState) {
      return isLockedRef.current;
    }

    const { instance, seriesModel, graphView, forceLayout, data, runtimeGraph } = runtimeState;
    const currentPositions = getCurrentNodePositions();

    graph.nodes.forEach((node, index) => {
      const position = currentPositions.get(node.id);
      if (position) {
        data.setItemLayout?.(index, position);
        runtimeGraph.getNodeByIndex(index).setLayout(position);
      }

      if (nextLocked) {
        forceLayout.setFixed(index);
        return;
      }

      forceLayout.setUnfixed(index);
    });

    if (nextLocked) {
      if (graphView._layoutTimeout) {
        clearTimeout(graphView._layoutTimeout);
      }
      graphView._layoutTimeout = null;
      graphView._layouting = false;
      graphView.updateLayout(seriesModel);
    } else {
      forceLayout.warmUp();
      if (!graphView._layouting && graphView._startForceLayoutIteration) {
        const layoutAnimation = Boolean(seriesModel.get?.(["force", "layoutAnimation"]));
        graphView._startForceLayoutIteration(forceLayout, instance, layoutAnimation);
      }
    }

    isLockedRef.current = nextLocked;
    requestAnimationFrame(() => {
      resizeChart();
      syncHighlight(selectedNodeId);
    });
    return isLockedRef.current;
  }, [getCurrentNodePositions, getRuntimeState, graph, resizeChart, selectedNodeId, syncHighlight]);

  useImperativeHandle(
    ref,
    () => ({
      resetView,
      fitView,
      toggleLayoutLock,
    }),
    [fitView, resetView, toggleLayoutLock],
  );

  useEffect(() => {
    syncHighlight(selectedNodeId);
  }, [selectedNodeId, syncHighlight]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) {
      return;
    }

    const observer = new ResizeObserver(() => {
      requestAnimationFrame(resizeChart);
    });
    observer.observe(host);

    requestAnimationFrame(resizeChart);

    return () => observer.disconnect();
  }, [resizeChart]);

  useEffect(() => {
    isLockedRef.current = false;
    requestAnimationFrame(() => {
      resizeChart();
      syncHighlight(selectedNodeId);
    });
  }, [graph, height, resizeChart, selectedNodeId, syncHighlight]);

  return (
    <div ref={hostRef} className="min-w-0 overflow-hidden" style={{ height, width: "100%" }}>
      <ReactECharts
        option={option}
        notMerge
        style={{ height: "100%", width: "100%" }}
        onChartReady={(instance) => {
          chartRef.current = instance;
          requestAnimationFrame(() => {
            resizeChart();
            syncHighlight(selectedNodeId);
          });
        }}
        onEvents={{
          click: (params: { data?: { id?: string; category?: string } }) => {
            if (params.data?.id) {
              onNodeClick?.(params.data.id, params.data.category ?? "concept");
            }
          },
          dblclick: (params: { data?: { id?: string; category?: string } }) => {
            if (params.data?.id) {
              onNodeDoubleClick?.(params.data.id, params.data.category ?? "concept");
            }
          },
        }}
      />
    </div>
  );
});
