export default function ErrorBox({ message, actionLabel, onAction }) {
  return (
    <div className="banner error" role="alert">
      <span>{message}</span>
      {onAction && (
        <button type="button" onClick={() => onAction()}>
          {actionLabel}
        </button>
      )}
    </div>
  )
}