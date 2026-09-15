import { useRef, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import {
  Field,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLabel,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";

export default function InputPreview() {
  const nameRef = useRef<HTMLInputElement>(null);
  const emailRef = useRef<HTMLInputElement>(null);
  const [errors, setErrors] = useState({ name: "", email: "" });
  const [saved, setSaved] = useState(false);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const name = nameRef.current;
    const email = emailRef.current;
    if (!name || !email) return;
    const next = {
      name: name.value.trim() ? "" : "请填写姓名。",
      email: email.validity.valueMissing
        ? "请填写邮箱。"
        : email.validity.typeMismatch
          ? "请输入有效的邮箱地址。"
          : "",
    };
    setErrors(next);
    setSaved(!next.name && !next.email);
    if (next.name) name.focus();
    else if (next.email) email.focus();
  }

  function resetFeedback() {
    setSaved(false);
    setErrors({ name: "", email: "" });
  }

  return (
    <section
      id="inputs"
      className="scroll-mt-6 space-y-6"
      aria-labelledby="input-title"
    >
      <div className="space-y-1">
        <h2 id="input-title" className="text-lg font-medium">
          Input & Field
        </h2>
        <p className="text-sm text-muted-foreground">
          36px 高度、6px 圆角、常规字重。标签、说明与状态明确关联。
        </p>
      </div>
      <div className="grid gap-6 md:grid-cols-2">
        <div className="space-y-6 rounded-xl border p-6">
          <h3 className="text-sm font-medium">基本输入</h3>
          <FieldGroup>
            <Field>
              <FieldLabel htmlFor="sample-agent-name">Agent 名称</FieldLabel>
              <Input
                id="sample-agent-name"
                placeholder="例如：研究 Agent"
                aria-describedby="sample-agent-description"
              />
              <FieldDescription id="sample-agent-description">
                让团队成员容易辨认它的职责。
              </FieldDescription>
            </Field>
            <Field>
              <FieldLabel htmlFor="sample-email">联系邮箱</FieldLabel>
              <Input
                id="sample-email"
                type="email"
                placeholder="name@example.com"
              />
            </Field>
            <Field>
              <FieldLabel htmlFor="sample-search">搜索</FieldLabel>
              <Input
                id="sample-search"
                type="search"
                placeholder="搜索成员或 Agent"
              />
            </Field>
          </FieldGroup>
        </div>
        <div className="space-y-6 rounded-xl border p-6">
          <h3 className="text-sm font-medium">字段状态</h3>
          <FieldGroup>
            <Field data-invalid>
              <FieldLabel htmlFor="sample-error">名称校验</FieldLabel>
              <Input
                id="sample-error"
                defaultValue="研究 Agent"
                aria-invalid="true"
                aria-describedby="sample-error-description"
              />
              <FieldError id="sample-error-description">
                该名称已存在，请更换名称。
              </FieldError>
            </Field>
            <Field data-disabled>
              <FieldLabel htmlFor="sample-disabled">不可编辑</FieldLabel>
              <Input
                id="sample-disabled"
                defaultValue="由企业管理员配置"
                disabled
              />
            </Field>
            <Field>
              <FieldLabel htmlFor="sample-readonly">只读标识</FieldLabel>
              <Input
                id="sample-readonly"
                defaultValue="agent_research_01"
                readOnly
                aria-describedby="sample-readonly-description"
              />
              <FieldDescription id="sample-readonly-description">
                可以选中和复制，不能修改。
              </FieldDescription>
            </Field>
          </FieldGroup>
        </div>
      </div>
      <form
        noValidate
        onSubmit={submit}
        onReset={resetFeedback}
        className="space-y-6 rounded-xl border p-6"
        aria-labelledby="input-form-title"
      >
        <div className="space-y-1">
          <h3 id="input-form-title" className="text-sm font-medium">
            表单交互
          </h3>
          <p className="text-sm text-muted-foreground">
            提交后检查必填项与邮箱格式，仅演示本地交互。
          </p>
        </div>
        <FieldGroup className="grid gap-6 md:grid-cols-2">
          <Field data-invalid={!!errors.name}>
            <FieldLabel htmlFor="demo-name">
              姓名{" "}
              <span aria-hidden="true" className="text-muted-foreground">
                *
              </span>
            </FieldLabel>
            <Input
              ref={nameRef}
              id="demo-name"
              name="name"
              autoComplete="off"
              placeholder="你的名字"
              required
              aria-invalid={!!errors.name}
              aria-describedby={errors.name ? "demo-name-error" : undefined}
              onChange={() => {
                setErrors((e) => ({ ...e, name: "" }));
                setSaved(false);
              }}
            />
            {errors.name && (
              <FieldError id="demo-name-error">{errors.name}</FieldError>
            )}
          </Field>
          <Field data-invalid={!!errors.email}>
            <FieldLabel htmlFor="demo-email">
              邮箱{" "}
              <span aria-hidden="true" className="text-muted-foreground">
                *
              </span>
            </FieldLabel>
            <Input
              ref={emailRef}
              id="demo-email"
              name="email"
              autoComplete="off"
              type="email"
              placeholder="name@example.com"
              required
              aria-invalid={!!errors.email}
              aria-describedby={errors.email ? "demo-email-error" : undefined}
              onChange={() => {
                setErrors((e) => ({ ...e, email: "" }));
                setSaved(false);
              }}
            />
            {errors.email && (
              <FieldError id="demo-email-error">{errors.email}</FieldError>
            )}
          </Field>
        </FieldGroup>
        <div className="flex flex-wrap items-center gap-3 border-t pt-5">
          <Button type="submit">验证表单</Button>
          <Button type="reset" variant="outline">
            重置
          </Button>
          <p className="text-sm text-muted-foreground" role="status">
            {saved ? "校验通过，未发送任何数据。" : "* 为必填项"}
          </p>
        </div>
      </form>
    </section>
  );
}
