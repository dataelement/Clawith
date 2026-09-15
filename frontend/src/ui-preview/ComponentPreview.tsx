import { useState } from "react";
import {
  IconArrowRight,
  IconLoader2,
  IconMoon,
  IconSun,
} from "@tabler/icons-react";
import { Button } from "@/components/ui/button";
import InputPreview from "./InputPreview";

export default function ComponentPreview() {
  const [dark, setDark] = useState(false);
  const [count, setCount] = useState(0);

  function toggleTheme() {
    const next = !dark;
    document.documentElement.classList.toggle("dark", next);
    setDark(next);
  }

  return (
    <main className="mx-auto min-h-svh max-w-5xl space-y-10 px-6 py-10 md:px-10">
      <header className="flex items-start justify-between gap-6 border-b pb-6">
        <div className="space-y-2">
          <p className="text-sm text-muted-foreground">Clawith / UI</p>
          <h1 className="text-2xl font-semibold tracking-tight">组件预览</h1>
          <p className="text-sm text-muted-foreground">
            shadcn 原生组件，逐个确认外观与交互。
          </p>
        </div>
        <Button
          variant="outline"
          size="icon"
          aria-label={dark ? "切换浅色主题" : "切换深色主题"}
          onClick={toggleTheme}
        >
          {dark ? <IconSun /> : <IconMoon />}
        </Button>
      </header>
      <nav aria-label="组件索引" className="flex gap-2">
        <Button asChild variant="outline" size="sm">
          <a href="#buttons">Button</a>
        </Button>
        <Button asChild variant="outline" size="sm">
          <a href="#inputs">Input &amp; Field</a>
        </Button>
      </nav>
      <section
        id="buttons"
        className="scroll-mt-6 space-y-6"
        aria-labelledby="button-title"
      >
        <div className="space-y-1">
          <h2 id="button-title" className="text-lg font-medium">
            Button
          </h2>
          <p className="text-sm text-muted-foreground">
            变体、尺寸、图标与禁用状态。
          </p>
        </div>
        <div className="space-y-8 rounded-xl border p-6">
          <div className="space-y-3">
            <h3 className="text-sm font-medium">变体</h3>
            <div className="flex flex-wrap items-center gap-3">
              <Button onClick={() => setCount((n) => n + 1)}>主要操作</Button>
              <Button variant="secondary">次要操作</Button>
              <Button variant="outline">描边按钮</Button>
              <Button variant="ghost">轻量操作</Button>
              <Button variant="destructive">删除</Button>
              <Button variant="link" asChild>
                <a href="#sizes">查看尺寸</a>
              </Button>
            </div>
            <p className="text-sm text-muted-foreground" role="status">
              已点击主要操作 {count} 次
            </p>
          </div>
          <div id="sizes" className="space-y-3">
            <h3 className="text-sm font-medium">尺寸</h3>
            <div className="flex flex-wrap items-center gap-3">
              <Button size="sm" variant="outline">
                小尺寸
              </Button>
              <Button variant="outline">默认尺寸</Button>
              <Button size="lg" variant="outline">
                大尺寸
              </Button>
              <Button size="icon" variant="outline" aria-label="下一步">
                <IconArrowRight />
              </Button>
            </div>
          </div>
          <div className="space-y-3">
            <h3 className="text-sm font-medium">状态</h3>
            <div className="flex flex-wrap items-center gap-3">
              <Button disabled>不可用</Button>
              <Button disabled aria-busy="true">
                <IconLoader2 className="animate-spin motion-reduce:animate-none" />
                处理中
              </Button>
              <Button variant="outline">
                继续
                <IconArrowRight />
              </Button>
            </div>
          </div>
        </div>
      </section>
      <InputPreview />
    </main>
  );
}
