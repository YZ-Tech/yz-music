import { IconButton, Tooltip } from '@mui/material'
import type { IconButtonProps, TooltipProps } from '@mui/material'
import {
  cloneElement,
  isValidElement,
  type ComponentPropsWithoutRef,
  type ElementType,
  type MouseEvent,
  type ReactNode,
} from 'react'

/** IconButton + Tooltip wrapper with sensible defaults for compact
 *  icon-only actions inside settings panels.
 *
 *  Four defaults baked in (so future-you doesn't have to remember
 *  them on every call site):
 *
 *    1. `component="span"` + `role="button"` — lets you nest this
 *       inside another `<button>` (e.g. MUI's `AccordionSummary`,
 *       which IS a button — HTML forbids `<button>` inside `<button>`).
 *       MUI's ButtonBase still wires up keyboard + focus correctly.
 *       Pass `component="button"` to override (form submit, etc.).
 *
 *    2. `onClick` is wrapped with `e.stopPropagation()` — clicks NEVER
 *       bubble to ancestor handlers. Prevents accidentally toggling
 *       the parent row/accordion when you click an action button
 *       inside it. Drop to plain `IconButton` + `Tooltip` if you ever
 *       need a button click to bubble (rare).
 *
 *    3. `size="small"` and the icon auto-gets `fontSize="small"` if
 *       not already set — keeps call sites tidy (`<RefreshIcon />`
 *       instead of `<RefreshIcon fontSize="small" />`).
 *
 *    4. `sx={{ p: 0.25 }}` — tighter padding than MUI's default. Pass
 *       your own `sx` to override; user sx wins over the default (they
 *       merge via the array form). Example: `sx={{ p: 0.5 }}` brings
 *       it back to MUI's small-size padding for a bigger touch target.
 *
 *  Tooltip:
 *    - Pass `label` (not `title`) — avoids collision with the native
 *      HTML `title` attribute.
 *    - Disabled buttons can't receive pointer events, so the wrapping
 *      `<span>` for the Tooltip is added automatically when needed.
 *
 *  Icon:
 *    <IconBtn label="…" icon={<RefreshIcon />} />          // prop form
 *    <IconBtn label="…"><RefreshIcon /></IconBtn>          // children form
 *    If both are supplied, `icon` wins. */

type IconBtnOwnProps = {
  /** Tooltip text — when set, wraps the IconButton in an MUI Tooltip. */
  label?: ReactNode
  /** Icon node — alternative to passing the icon as children. Wins
   *  over `children` when both are set. */
  icon?: ReactNode
  /** Escape hatch for passing arbitrary props to the wrapping MUI
   *  Tooltip — e.g. `placement`, `arrow`, or `slotProps` for a wider
   *  long-form tooltip. The `title` prop is set internally from
   *  `label`, so don't pass it here. */
  tooltipProps?: Omit<Partial<TooltipProps>, 'title' | 'children'>
}

/** Polymorphic over `component` so `<IconBtn component={RouterLink} to="…" />`
 *  type-checks against RouterLink's props (and any other element type you
 *  pass). Defaults to `'span'` to keep the "nest-inside-button" behavior. */
export type IconBtnProps<C extends ElementType = 'span'> = IconBtnOwnProps &
  Omit<IconButtonProps, 'component'> & {
    component?: C
  } & Omit<ComponentPropsWithoutRef<C>, keyof IconBtnOwnProps | keyof IconButtonProps | 'component'>

/** If `node` is a valid React element without an explicit `fontSize`,
 *  clone it with `fontSize="small"`. Leaves non-element children
 *  (strings, fragments, arrays) untouched. */
const withDefaultIconSize = (node: ReactNode): ReactNode => {
  if (!isValidElement<{ fontSize?: string }>(node)) return node
  if (node.props.fontSize !== undefined) return node
  return cloneElement(node, { fontSize: 'small' })
}

/** IconBtn: IconButton + Tooltip
 *
 * note: e.stopPropagation() by default.
 *
 * see `IconBtnProps` for docs.
 */
export function IconBtn<C extends ElementType = 'span'>({
  label,
  icon,
  tooltipProps,
  size = 'small',
  component,
  role = 'button',
  disabled,
  children,
  onClick,
  sx,
  ...rest
}: IconBtnProps<C>) {
  const content = withDefaultIconSize(icon ?? children)
  const handleClick = onClick
    ? (e: MouseEvent<HTMLButtonElement>) => {
        e.stopPropagation()
        onClick(e)
      }
    : undefined
  // Merge default padding with user sx via the array form — entries
  // later in the array win, so any `p` the user passes overrides the
  // default. Works for both single-object and array/function sx.
  const mergedSx = Array.isArray(sx)
    ? [{ p: 0.25 }, ...sx]
    : [{ p: 0.25 }, sx]
  const btn = (
    <IconButton
      size={size}
      component={component ?? 'span'}
      role={role}
      disabled={disabled}
      onClick={handleClick}
      sx={mergedSx}
      {...rest}
    >
      {content}
    </IconButton>
  )
  if (!label) return btn
  return (
    <Tooltip title={label} {...tooltipProps}>
      {disabled ? <span>{btn}</span> : btn}
    </Tooltip>
  )
}
