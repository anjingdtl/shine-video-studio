import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode,
} from 'react';
import { ActionRail, type RailAction, type RailBlocker, type RailRunning } from './ActionRail';

/** 面板向页面操作栏注册的主/次操作。priority 越小越优先成为页面唯一 primary；
 *  其余面板的 primary 自动降为次级置顶（设计方案 §3.1 的一页双 must 排序规则）。 */
export type StageRegistration = {
  priority: number;
  primary?: RailAction | null;
  secondary?: RailAction[];
  running?: RailRunning | null;
};

type RailContextValue = {
  submit: (id: string, registration: StageRegistration) => void;
  remove: (id: string) => void;
};

const RailContext = createContext<RailContextValue | null>(null);

/** 面板据此判断主操作是否已收进页面级吸顶栏：有栏时内容区不再渲染 primary，
 *  无栏（如面板被单独渲染的测试）时面板照常渲染自己的主操作按钮。 */
export function useRailPresent(): boolean {
  return useContext(RailContext) !== null;
}

/** 面板用它把当前主操作注册到页面级吸顶栏。deps 必须覆盖动作闭包用到的全部状态，
 *  状态变化时重新注册，保证栏内按钮的 onClick/loading/disabled 与面板同步。 */
export function useStageRail(id: string, registration: StageRegistration, deps: unknown[]) {
  const ctx = useContext(RailContext);
  useEffect(() => {
    ctx?.submit(id, registration);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ctx, id, ...deps]);
  useEffect(() => () => { ctx?.remove(id); }, [ctx, id]);
}

type ProviderProps = {
  stageId: string;
  stageLabel?: string;
  blockers?: RailBlocker[];
  progress?: { done: number; total: number; label: string };
  children: ReactNode;
};

/** 页面级三区骨架：内容区 + 唯一吸顶操作栏。完成态/阻塞由调用方从 /journey 传入，
 *  这里不自行推断任何「已完成」。 */
export function StageRailProvider({ stageId, stageLabel, blockers, progress, children }: ProviderProps) {
  const registrations = useRef(new Map<string, StageRegistration>());
  const [, setVersion] = useState(0);
  const submit = useCallback((id: string, registration: StageRegistration) => {
    registrations.current.set(id, registration);
    setVersion((version) => version + 1);
  }, []);
  const remove = useCallback((id: string) => {
    if (registrations.current.delete(id)) setVersion((version) => version + 1);
  }, []);
  const ctx = useMemo(() => ({ submit, remove }), [submit, remove]);

  const sorted = [...registrations.current.values()].sort((a, b) => a.priority - b.priority);
  const primaryReg = sorted.find((row) => row.running || row.primary) ?? null;
  const primary = primaryReg?.primary ?? null;
  const running = primaryReg?.running ?? null;
  const secondary: RailAction[] = [
    ...sorted.filter((row) => row !== primaryReg && row.primary).map((row) => row.primary as RailAction),
    ...sorted.flatMap((row) => row.secondary ?? []),
  ];

  return (
    <RailContext.Provider value={ctx}>
      <div className="stage-layout">
        <div className="stage-content">{children}</div>
        <ActionRail
          stageId={stageId}
          stageLabel={stageLabel}
          primary={primary}
          running={running}
          secondary={secondary}
          blockers={blockers}
          progress={progress}
        />
      </div>
    </RailContext.Provider>
  );
}