import { forwardRef, type InputHTMLAttributes } from 'react';

interface Props extends InputHTMLAttributes<HTMLInputElement> {
    label?: string;
    /** Adopt italic serif typography for the input value */
    serif?: 'md' | 'lg';
}

const HairlineInput = forwardRef<HTMLInputElement, Props>(function HairlineInput(
    { label, serif, className, id, ...rest },
    ref,
) {
    const inputId = id || (label ? `atlas-input-${label.replace(/\s+/g, '-').toLowerCase()}` : undefined);
    const inputClasses = [
        'atlas-input',
        serif === 'md' && 'atlas-input--serif',
        serif === 'lg' && 'atlas-input--serif-lg',
        className,
    ].filter(Boolean).join(' ');

    return (
        <div className="atlas-input-wrap">
            {label && (
                <label className="atlas-input-label" htmlFor={inputId}>
                    {label}
                </label>
            )}
            <input ref={ref} id={inputId} className={inputClasses} {...rest} />
        </div>
    );
});

export default HairlineInput;
