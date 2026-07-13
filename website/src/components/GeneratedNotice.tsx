import React from "react";

/**
 * Banner shown at the top of every auto-generated reference page. It makes the
 * "do not edit by hand" contract visible to readers and to anyone tempted to
 * patch a generated file instead of the source contract.
 */
export default function GeneratedNotice({
  source,
  command,
}: {
  source: string;
  command: string;
}): React.JSX.Element {
  return (
    <div className="generated-banner" role="note">
      <strong>Сгенерировано автоматически — не редактируйте вручную.</strong>
      <br />
      Эта страница собрана из проверенных публичных контрактов:{" "}
      <code>{source}</code>. Обновляется командой <code>{command}</code>. Изменения вносите в
      исходный контракт, затем перегенерируйте страницу.
    </div>
  );
}
